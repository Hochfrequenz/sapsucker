"""Sampled observation of a live SAP GUI session.

While a human records their clicks with SAP GUI's built-in recorder
(``Alt+F12`` -> *Script Recording and Playback*), a :class:`SessionMonitor`
watches the same session and reports what changed, with timestamps. Pairing the
two gives you what neither has alone: the recording says *what* was done, the
monitor says *when*, and captures things a recording structurally cannot —
pauses, revisits, and the fact that a value changed back.

What a recording omits, and this supplies:

* **Timestamps.** A recorded ``.vbs`` has none, so where the human paused —
  and therefore where they were deciding — is unrecoverable from it.
* **The editing process.** A recording captures the final assignment, not the
  route to it. A human who tabs back to re-check a field leaves no trace in the
  ``.vbs`` at all.
* **Outcome hints.** Recordings carry no status bar, no messages, and no
  success signal, so a run that failed midway looks identical to one that
  worked. A main-window title change at save time is crude, but it is a signal.

Do not poll ``GuiSessionInfo.RoundTrips`` for this. It looks like a cumulative
counter and is not — it is a per-request statistic like ``ResponseTime`` beside
it, observed going ``4 -> 2 -> 1`` on a live session. Worse, server round trips
are invisible for intra-screen actions: a 17-line SE16N recording containing 10
``firstVisibleRow`` assignments produced four events, being one baseline plus
three screen transitions, and not a single scroll.

Thread safety
-------------
COM is STA: every call must happen on the thread that called
``pythoncom.CoInitialize()`` (see :mod:`sapsucker._com`).
:meth:`SessionMonitor.samples` is a generator, so the caller owns the loop and
therefore the thread — this class never starts one. A monitor loop occupies its
thread for its whole lifetime, so do not run one on a thread another consumer
uses for COM work, and never from inside a shared COM worker.

Known limits, by design rather than oversight:

* **Sampling loses fast repeats.** At 200 ms, a journey with six
  ``firstVisibleRow`` assignments produced four samples showing a change.
  Positions are recoverable from the full series; event *counts* are not.
* **Not every recorded line moves observable state.** ``resizeWorkingPane``
  (recorder boilerplate) and a trailing ``setFocus``/``caretPosition`` on a
  field that already had focus produce no sample change, and two button presses
  inside one modal collapse into one transition.
* **Same-titled chained dialogs are indistinguishable**, so popup counts stay
  ambiguous.
* **Graceful degradation is unit-tested, not field-tested.** If a read fails the
  values become :data:`UNREADABLE` and sampling continues — but three attempts to
  provoke that against a live session did not manage it. Closing a mode left the
  proxy valid, and closing SAP GUI raises its own confirmation dialog, which keeps
  the session alive while it waits — the log's last sample read
  ``program=SAPLSPO1`` (SAP's popup program) with real values, 24 s after the
  close was initiated. So the real COM failure mode has not been observed; it is
  only known that the code handles a raising read.

Example::

    from sapsucker import SapGui
    from sapsucker.monitor import SessionMonitor, Watch

    session = SapGui.connect().connections[0].sessions[0]
    monitor = SessionMonitor(session, watches=[Watch(element_id="wnd[0]/shellcont/shell", prop="FirstVisibleRow")])
    for sample in monitor.samples():
        if sample.changed:
            print(sample.elapsed, sample.changed)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import count
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ABSENT", "SCHEMA_VERSION", "UNREADABLE", "Sample", "SessionMonitor", "Watch"]

SCHEMA_VERSION = 1
"""Version of the emitted sample format.

The JSONL becomes a contract the moment anything parses it — field names,
sentinel spellings, the ISO-8601 duration encoding. Stamping every record means
a consumer can detect an old file; adding this after the first consumer has
locked onto field names would be a breaking change with no way to tell which
format a given file is in. Bump on any change to the record shape.
"""

_T = TypeVar("_T")

UNREADABLE = "<unreadable>"
"""A property read raised. Transient — the previous value is carried forward.

A SAP value that is literally this string is indistinguishable from a failed
read and will be carried forward. Accepted: no real screen text is this.
"""

ABSENT = "<absent>"
"""``find_by_id`` found nothing. A real state (no modal open), never carried forward."""


class Watch(BaseModel):
    """An extra COM property to sample on a named element."""

    model_config = ConfigDict(frozen=True)

    element_id: str = Field(
        description="SAP GUI element id.",
        examples=["wnd[0]/shellcont/shell", "wnd[1]", "wnd[0]/usr/subA02P01:SAPLBUD0:1130/cmbBUS000FLDS-TITLE_MEDI"],
    )
    prop: str = Field(
        description=(
            "COM property name, PascalCase as the scripting API spells it. Read with str(), "
            "so a property returning a COM object rather than a scalar is logged as its repr "
            "(e.g. '<PyIDispatch...>') with no signal that it differs from a real value."
        ),
        examples=["FirstVisibleRow", "CurrentCellRow", "Text"],
    )

    @property
    def key(self) -> str:
        """Key this watch appears under in :attr:`Sample.values`."""
        return f"{self.element_id}:{self.prop}"

    @classmethod
    def parse(cls, raw: str) -> Watch:
        """Parse ``"element_id:ComProperty"``.

        Splits on the *last* colon, because element ids contain colons
        themselves (BDT subscreen paths such as ``subA02P01:SAPLBUD0:1130``).

        Raises:
            ValueError: If *raw* has no colon, or an empty id or property.
        """
        element_id, _, prop = raw.rpartition(":")
        if not element_id or not prop:
            raise ValueError(f"expected 'element_id:ComProperty', got {raw!r}")
        return cls(element_id=element_id, prop=prop)


class Sample(BaseModel):
    """One observation of session state.

    Durations are :class:`~datetime.timedelta`, which pydantic serialises as
    ISO-8601 durations (``PT3.25S``) in JSON mode — so a log line carries a
    self-describing duration rather than a bare number needing a unit convention.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(
        default=SCHEMA_VERSION,
        description="Version of the record format, so a consumer can detect an older file.",
        examples=[1],
    )
    seq: int = Field(description="Zero-based sample counter.", examples=[0, 42])
    at: datetime = Field(
        description="Local-time timestamp of the sample.",
        # Microsecond precision, unlike the durations: a timestamp is an actual
        # observation, so it is recorded as read rather than rounded.
        examples=["2026-08-26T16:47:37.113528+02:00"],
    )
    elapsed: timedelta = Field(
        description="Time since monitoring started.",
        examples=["PT6.109S", "PT41.312S"],
    )
    values: dict[str, Any] = Field(
        description="Observed state, keyed by field name or Watch.key.",
        examples=[{"transaction": "BP", "screen_number": 3000, "focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/okcd"}],
    )
    changed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Keys whose value differs from the previous sample. Empty on the baseline sample.",
        examples=[(), ("focus_id",), ("transaction", "program", "screen_number", "focus_id")],
    )
    gap_since_change: timedelta | None = Field(
        default=None,
        description=(
            "Time since the previous *changing* sample, or None if this sample changed nothing. "
            "This is the pause signal: a long gap marks where the human was deciding."
        ),
        examples=["PT6.328S", None],
    )

    def as_record(self) -> dict[str, Any]:
        """Flatten to a JSONL-friendly dict, values inlined alongside metadata.

        Inlined rather than nested so each log line is one flat row, which is
        what makes the series analysable with ordinary tools.
        """
        record: dict[str, Any] = self.model_dump(mode="json", exclude={"values"})
        record.update(self.values)
        return record


@dataclass
class SessionMonitor:
    """Samples observable state of a live session at a fixed interval.

    A dataclass rather than a pydantic model, unlike :class:`Watch` and
    :class:`Sample`: it holds a live COM session handle, which has nothing to
    validate or serialise, and modelling it would mean ``arbitrary_types_allowed``
    for no benefit. The data crossing the boundary is pydantic; the service is not.

    Args:
        session: A ``GuiSession``.
        watches: Extra element properties to sample. Element ids are resolved
            every sample, so an element that only exists sometimes — a modal,
            for instance — is reported as :data:`ABSENT` while it is gone.
        interval: Seconds between samples. Lower catches more fast repeats at
            the cost of more COM traffic.
    """

    session: Any
    watches: Sequence[Watch] = field(default_factory=tuple)
    interval: float = 0.2

    def __post_init__(self) -> None:
        if self.interval < 0:
            raise ValueError(f"interval must be >= 0, got {self.interval}")

    def read_once(self) -> dict[str, Any]:
        """Read one raw fingerprint. Cheap property gets only, never ``dump_tree``."""
        info = _safe(lambda: self.session.info)
        if isinstance(info, str):
            # The whole Info object was unreadable, so degrade every field it
            # would have supplied. Reading through the sentinel would raise an
            # AttributeError that _safe happens to swallow; relying on that is
            # accidental, and the type checker is right to object.
            base: dict[str, Any] = dict.fromkeys(("transaction", "program", "screen_number"), UNREADABLE)
        else:
            base = {
                "transaction": _safe(lambda: info.transaction),
                "program": _safe(lambda: info.program),
                "screen_number": _safe(lambda: info.screen_number),
            }
        values: dict[str, Any] = {
            **base,
            "busy": _safe(lambda: self.session.busy),
            "focus_id": _safe(self._read_focus_id),
        }
        for watch in self.watches:
            values[watch.key] = _safe(partial(self._read_watch, watch))
        return values

    def samples(self) -> Iterator[Sample]:
        """Yield samples forever, one every ``interval`` seconds.

        The caller owns the loop: break out of it, or wrap it in a timeout. The
        generator sleeps between samples, so it also owns the thread.

        Yields:
            Every sample, not only the ones that changed. The full series is
            what makes the log analysable after the fact; a change-detector was
            the bug in the first version of this.
        """
        started = time.monotonic()
        previous: dict[str, Any] | None = None
        last_change_at = started

        for seq in count():
            values = self.read_once()
            now = time.monotonic()

            if previous is not None:
                _carry_forward_unreadable(values, previous)
                changed = tuple(key for key, value in values.items() if previous.get(key) != value)
            else:
                changed = ()

            gap: timedelta | None = None
            if changed:
                gap = timedelta(seconds=round(now - last_change_at, 3))
                last_change_at = now

            yield Sample(
                seq=seq,
                at=datetime.now(UTC).astimezone(),
                elapsed=timedelta(seconds=round(now - started, 3)),
                values=values,
                changed=changed,
                gap_since_change=gap,
            )

            previous = values
            # Sleep the remainder of the period, not the full interval: the
            # consumer's work is part of the period, so a flat sleep would add it
            # on top. The read itself is NOT compensated — `now` is captured after
            # read_once — so the achieved period is interval + read time
            # (230 ms measured at a nominal 200 ms).
            time.sleep(max(0.0, self.interval - (time.monotonic() - now)))

    def _read_focus_id(self) -> str:
        # Read through .com rather than the gui_focus wrapper: this runs every
        # sample, and the wrapper costs a factory type lookup per call.
        window = self.session.find_by_id("wnd[0]", raise_error=False)
        if window is None:
            return ABSENT
        return str(window.com.GuiFocus.Id)

    def _read_watch(self, watch: Watch) -> str:
        element = self.session.find_by_id(watch.element_id, raise_error=False)
        if element is None:
            return ABSENT  # e.g. wnd[1] with no modal open: a state, not a failure
        return str(getattr(element.com, watch.prop))


def _safe(read: Callable[[], _T]) -> _T | str:
    """Best-effort read: a mid-transition failure must not stop monitoring.

    Returns the read value, or :data:`UNREADABLE` if it raised — hence the
    ``_T | str`` return, which makes the sentinel visible to type checkers
    instead of hiding behind ``Any``.
    """
    try:
        return read()
    except Exception:  # pylint: disable=broad-exception-caught
        return UNREADABLE


def _carry_forward_unreadable(values: dict[str, Any], previous: dict[str, Any]) -> None:
    """Replace a failed read with the last good value, in place.

    A property get that raises because the screen is mid-transition is not a
    state change. Treating it as one produced phantom events — one observed
    sample's only "change" was focus becoming unreadable. :data:`ABSENT` is
    neither carried forward nor used as a source: an element genuinely not being
    there is real, so masking it would hide a modal closing, and reporting a
    failed read as ``<absent>`` would defeat having two sentinels at all.
    """
    for key, value in list(values.items()):
        if value != UNREADABLE:
            continue
        carried = previous.get(key, UNREADABLE)
        if carried not in (UNREADABLE, ABSENT):  # ABSENT is a state, not a good value
            values[key] = carried
