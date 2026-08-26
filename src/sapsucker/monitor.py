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
``pythoncom.CoInitialize()`` (see :mod:`sapsucker._com`). :meth:`
SessionMonitor.samples` is a generator, so the caller owns the loop and
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

Example::

    from sapsucker import SapGui
    from sapsucker.monitor import SessionMonitor, Watch

    session = SapGui.connect().connections[0].sessions[0]
    monitor = SessionMonitor(session, watches=[Watch("wnd[0]/shellcont/shell", "FirstVisibleRow")])
    for sample in monitor.samples():
        if sample.changed:
            print(sample.elapsed_s, sample.changed)
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from itertools import count
from typing import Any

__all__ = ["ABSENT", "UNREADABLE", "Sample", "SessionMonitor", "Watch"]

UNREADABLE = "<unreadable>"
"""A property read raised. Transient — the previous value is carried forward.

A SAP value that is literally this string is indistinguishable from a failed
read and will be carried forward. Accepted: no real screen text is this.
"""

ABSENT = "<absent>"
"""``find_by_id`` found nothing. A real state (no modal open), never carried forward."""


@dataclass(frozen=True)
class Watch:
    """An extra COM property to sample on a named element.

    Args:
        element_id: SAP GUI element id, e.g. ``"wnd[0]/shellcont/shell"``.
        prop: COM property name in PascalCase, e.g. ``"FirstVisibleRow"``.
    """

    element_id: str
    prop: str

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


@dataclass(frozen=True)
class Sample:
    """One observation of session state.

    Attributes:
        seq: Zero-based sample counter.
        at: Local-time ISO 8601 timestamp, millisecond precision.
        elapsed_s: Seconds since monitoring started.
        values: The observed state, keyed by field name or :attr:`Watch.key`.
        changed: Keys whose value differs from the previous sample. Empty on the
            first sample, which is the baseline.
        gap_since_change_s: Seconds since the previous *changing* sample, or
            ``None`` if this sample changed nothing. This is the pause signal.
    """

    seq: int
    at: str
    elapsed_s: float
    values: dict[str, Any]
    changed: tuple[str, ...] = ()
    gap_since_change_s: float | None = None

    def as_record(self) -> dict[str, Any]:
        """Flatten to a JSONL-friendly dict, values inlined alongside metadata."""
        return {
            "seq": self.seq,
            "at": self.at,
            "elapsed_s": self.elapsed_s,
            "changed": list(self.changed),
            "gap_since_change_s": self.gap_since_change_s,
            **self.values,
        }


@dataclass
class SessionMonitor:
    """Samples observable state of a live session at a fixed interval.

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
        values: dict[str, Any] = {
            "transaction": _safe(lambda: info.transaction),
            "program": _safe(lambda: info.program),
            "screen_number": _safe(lambda: info.screen_number),
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

            gap: float | None = None
            if changed:
                gap = round(now - last_change_at, 3)
                last_change_at = now

            yield Sample(
                seq=seq,
                at=datetime.now(UTC).astimezone().isoformat(timespec="milliseconds"),
                elapsed_s=round(now - started, 3),
                values=values,
                changed=changed,
                gap_since_change_s=gap,
            )

            previous = values
            # Sleep the remainder, not the full interval: reads plus the consumer's
            # work are part of the period, so a flat sleep undershoots the rate.
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


def _safe(read: Any) -> Any:
    """Best-effort read: a mid-transition failure must not stop monitoring."""
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
