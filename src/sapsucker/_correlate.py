"""Correlate a recorded ``.vbs`` with a monitor JSONL and an optional transcript.

Phase 5 of the recording→artefact plan (``docs/superpowers/specs/2026-10-03-
recording-to-skill-pipeline-design.md``), issue #126. A recording says *what*
was done but carries no timestamps; the monitor JSONL says *when*, plus the
sampled screen/focus/status-bar state; an optional narration transcript says
*why*. This module joins the three into one merged timeline: for every recorded
step, the timestamp window in which its observable counterpart moved, the
status-bar text in that window, and the transcript excerpts falling inside it.

The matcher walks the recorded steps in order and timestamps each by the first
strategy that fits (see :func:`correlate`). One monotonic cursor over sample
indices keeps steps from matching backwards: a later step can share an
earlier step's anchor (sub-interval actions collapse — the known sampling
limit, flagged as such) but never lands before it.

Pure library code: no COM involved, everything here is testable in CI against
synthetic logs and the committed corpus in ``docs/spike/``.

Example::

    from sapsucker._correlate import correlate, load_monitor_log
    from sapsucker._recording import Recording

    rec = Recording.load("docs/spike/journey3_bp.vbs")
    log = load_monitor_log(open("timing.jsonl", encoding="utf-8"))
    timeline = correlate(rec, log)
    print(timeline.to_markdown())
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sapsucker._recording import Recording, RecordingStep

__all__ = [
    "CorrelatedTimeline",
    "MonitorLog",
    "MonitorSample",
    "TimelineStep",
    "TranscriptEntry",
    "correlate",
    "load_monitor_log",
]

_UNPACKED_KEYS = frozenset(
    {
        "schema_version",
        "seq",
        "at",
        "elapsed",
        "elapsed_s",
        "changed",
        "gap_since_change",
        "gap_since_change_s",
        "record_type",
        "origin_at",
        "recording_file",
        "recorder_skew",
    }
)
_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
_UNREADABLE = "<unreadable>"
_ABSENT = "<absent>"
_SENTINELS = frozenset({_UNREADABLE, _ABSENT})


@dataclass(frozen=True)
class MonitorSample:
    """One flattened JSONL sample, normalized across the two schema generations."""

    seq: int
    #: Seconds since the sampler origin (already parsed from ``elapsed_s`` or
    #: the ISO-8601 duration ``elapsed``).
    elapsed: float
    changed: frozenset[str]
    values: dict[str, Any]


@dataclass(frozen=True)
class MonitorLog:
    samples: list[MonitorSample]
    #: ``--record`` logs open with a header record carrying the measured skew
    #: between the sampler origin and the recorder start.
    recorder_skew: float | None = None
    #: True for a v2 log (no header): correlating it against a recording
    #: assumes both started together, which the manual pairing procedure
    #: (start monitor, start recorder by hand) makes approximate.
    clock_origin_assumed: bool = True


class _LineError(ValueError):
    """A ValueError whose message already names its line."""


def _parse_elapsed(d: dict[str, Any], line_no: int) -> float:
    if "elapsed_s" in d:
        # ``float(None)`` and friends raise TypeError; the loader turns that into
        # a line-numbered ValueError.
        return float(d["elapsed_s"])
    raw = d.get("elapsed")
    m = _ISO_DURATION.match(raw) if isinstance(raw, str) else None
    # ``P`` / ``PT`` alone match the pattern but carry no component.
    if not m or not any(m.groups()):
        raise _LineError(f"line {line_no}: sample {d.get('seq')}: no usable elapsed field ({raw!r})")
    days, hours, minutes, seconds = m.groups()
    return int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def load_monitor_log(lines: list[str]) -> MonitorLog:
    """Parse monitor JSONL lines (schema v2 flat, or v3+ with a header record).

    Raises:
        ValueError: If a line is not JSON, not an object, or a sample has no
            usable elapsed field; or if the log holds no samples at all.
    """
    samples: list[MonitorSample] = []
    recorder_skew: float | None = None
    for line_no, line in enumerate(lines, 1):
        raw = line.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: not JSON: {raw[:80]!r}") from exc
        if not isinstance(d, dict):
            raise ValueError(f"line {line_no}: expected a JSON object, got {type(d).__name__}")
        try:
            if d.get("record_type") == "header":
                skew = d.get("recorder_skew")
                recorder_skew = float(skew) if skew is not None else None
                continue
            changed = d.get("changed")
            if changed is None:
                changed = []
            if not isinstance(changed, list) or not all(isinstance(c, str) for c in changed):
                raise TypeError(f"'changed' must be a list of strings, got {changed!r}")
            samples.append(
                MonitorSample(
                    seq=int(d.get("seq", len(samples))),
                    elapsed=_parse_elapsed(d, line_no),
                    changed=frozenset(changed),
                    values={k: v for k, v in d.items() if k not in _UNPACKED_KEYS},
                )
            )
        except _LineError:
            raise
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"line {line_no}: invalid field in monitor record: {exc}") from exc
    if not samples:
        raise ValueError("no samples in monitor log")
    return MonitorLog(samples=samples, recorder_skew=recorder_skew, clock_origin_assumed=recorder_skew is None)


@dataclass(frozen=True)
class TranscriptEntry:
    """A normalized narration entry: text spoken between two timeline points."""

    t_start: float
    t_end: float
    text: str


@dataclass(frozen=True)
class TimelineStep:
    """One recorded step with its matched window in the monitor log."""

    line_no: int
    element_id: str
    member: str
    args: tuple[str, ...] | None
    #: Which strategy timestamped this step — ``exact-focus``, ``ddic-suffix``,
    #: ``watch-run``, ``modal-bracket``, ``fingerprint-screen``,
    #: ``fingerprint-title``, ``recorder-boilerplate`` or ``unmatched``.
    strategy: str
    confidence: str
    t_start: float | None = None
    t_end: float | None = None
    #: Authoring flags: ``keyboard-anchor``, ``layout-sensitive``,
    #: ``sub-interval-collapse``, ``value-mismatch``, ``suffix-ambiguous``.
    #: Clock alignment is run metadata (``CorrelatedTimeline.clock_origin_assumed``),
    #: not a step flag.
    flags: tuple[str, ...] = ()
    #: Last readable status-bar text at or before the matched sample; None when
    #: unmatched or when the log has no status-bar keys. A message this step
    #: causes one sample *after* its anchor shows up on the next step instead,
    #: so treat it as context, not as this step's outcome (#131).
    sbar_text: str | None = None
    #: Transcript excerpts intersecting the matched window, verbatim.
    transcript: tuple[str, ...] = ()


@dataclass(frozen=True)
class CorrelatedTimeline:
    """The merged timeline: one entry per recorded step, plus run metadata."""

    steps: list[TimelineStep]
    recording_path: str | None = None
    recorder_skew: float | None = None
    clock_origin_assumed: bool = True
    strategy_counts: dict[str, int] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        """One flat JSON object per step — the machine-readable timeline.

        Field names are the dataclass fields; ``args`` serializes as a list
        (JSON has no tuples) and unset times stay ``null`` rather than being
        dropped, so a consumer can distinguish "no window" from 0. The run's
        ``recorder_skew`` and ``clock_origin_assumed`` are repeated on every
        record so a single line is self-describing.
        """
        lines = []
        for step in self.steps:
            d = {
                "line_no": step.line_no,
                "element_id": step.element_id,
                "member": step.member,
                "args": list(step.args) if step.args is not None else None,
                "strategy": step.strategy,
                "confidence": step.confidence,
                "t_start": step.t_start,
                "t_end": step.t_end,
                "flags": list(step.flags),
                "sbar_text": step.sbar_text,
                "transcript": list(step.transcript),
                "recorder_skew": self.recorder_skew,
                "clock_origin_assumed": self.clock_origin_assumed,
            }
            lines.append(json.dumps(d, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

    def to_markdown(self) -> str:
        """The human-readable merged journey document.

        Acceptance bar (plan doc): "the timeline reads as a plausible
        description of the task to someone who wasn't there" — hence the
        run-metadata header (clock alignment!) before the per-step table.
        """
        out: list[str] = ["# Correlated journey timeline", ""]
        if self.recording_path:
            out.append(f"Recording: `{self.recording_path}`")
        if self.recorder_skew is not None:
            out.append(
                f"Recorder started {self.recorder_skew:.3f}s after the sampler origin (measured, `--record` header)."
            )
        if self.clock_origin_assumed:
            out.append(
                "Clock alignment: **assumed** — this log has no `--record` header, so monitor origin ≈ "
                "recording start (manual pairing). Timestamps carry that skew."
            )
        counts = ", ".join(f"{n} {name}" for name, n in sorted(self.strategy_counts.items()))
        out.append(f"Steps: {counts}.")
        out.append("")
        out.append("| line | member | t_start | t_end | strategy | confidence | flags | status bar | transcript |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for step in self.steps:
            t_start = "—" if step.t_start is None else f"{step.t_start:.3f}"
            t_end = "—" if step.t_end is None else f"{step.t_end:.3f}"
            sbar = _md_cell((step.sbar_text or "")[:40])
            narr = _md_cell(" / ".join(step.transcript))
            out.append(
                f"| {step.line_no} | {step.member} | {t_start} | {t_end} "
                f"| {step.strategy} | {step.confidence} | {', '.join(step.flags)} | {sbar} | {narr} |"
            )
        out.append("")
        return "\n".join(out)


def _md_cell(text: str) -> str:
    """Make free text safe inside one markdown table cell."""
    return " ".join(text.replace("\\", "\\\\").replace("|", "\\|").split())


#: Recorder boilerplate that moves no observable state — labelled, never matched.
_BOILERPLATE_MEMBERS = frozenset({"resizeWorkingPane", "maximize"})
#: Members that inherit the preceding matched step's window: they act on the
#: field the previous step just filled and happen within one sampling interval.
_COLLAPSE_MEMBERS = frozenset({"setFocus", "caretPosition"})
#: Members whose observable effect is a screen transition rather than a focus move.
_SCREEN_MEMBERS = frozenset({"press", "sendVKey", "select"})
_STRATEGY_CONFIDENCE = {
    "exact-focus": "high",
    "ddic-suffix": "medium",
    "watch-run": "high",
    "modal-bracket": "medium",
    "fingerprint-screen": "medium",
    "fingerprint-title": "medium",
    "recorder-boilerplate": "high",
    "unmatched": "low",
}


def _last_segment(element_id: str) -> str:
    return element_id.rsplit("/", 1)[-1]


def _ddic_suffix(segment: str) -> str | None:
    """The DDIC field name after the last ``-`` (``…-TEL_NUMBER`` → ``TEL_NUMBER``)."""
    return segment.rsplit("-", 1)[-1] if "-" in segment else None


def _scan(log: MonitorLog, start: int, pred: Callable[[MonitorSample], bool]) -> int | None:
    """First sample index >= start satisfying pred, else None."""
    for i in range(start, len(log.samples)):
        if pred(log.samples[i]):
            return i
    return None


def _focus_changed_to(sample: MonitorSample, pred: Callable[[str], bool]) -> bool:
    if "focus_id" not in sample.changed:
        return False
    focus = sample.values.get("focus_id")
    if not isinstance(focus, str) or focus in _SENTINELS:
        return False
    return bool(pred(_last_segment(focus)))


def _focus_on_segment(segment: str) -> Callable[[MonitorSample], bool]:
    return lambda sample: _focus_changed_to(sample, lambda seg: seg == segment)


def _focus_on_suffix(suffix: str | None) -> Callable[[MonitorSample], bool]:
    return lambda sample: _focus_changed_to(sample, lambda seg: _ddic_suffix(seg) == suffix)


def _is_repeat_anchor(prev: TimelineStep | None, segment: str, suffix: str | None = None) -> bool:
    """True when the previous *matched* step (unmatched and boilerplate rows excluded) was an
    exact-focus or ddic-suffix match on the same field (or, for a suffix match,
    a field with the same DDIC suffix) — i.e. this step would silently share its
    anchor."""
    if prev is None or prev.strategy not in {"exact-focus", "ddic-suffix"} or prev.t_start is None:
        return False
    prev_segment = _last_segment(prev.element_id)
    return prev_segment == segment or (suffix is not None and _ddic_suffix(prev_segment) == suffix)


def _last_matched(steps_out: list[TimelineStep]) -> TimelineStep | None:
    """The most recent step that actually took an anchor.

    Unmatched and boilerplate rows move nothing, so a repeat edit after one of
    them still shares the previous matched step's sample.
    """
    for step in reversed(steps_out):
        if step.strategy not in {"unmatched", "recorder-boilerplate"}:
            return step
    return None


def _consumes_event(step: RecordingStep) -> bool:
    """Whether *step* competes for a focus event.

    ``setFocus``/``caretPosition`` inherit the previous step's window and
    boilerplate moves nothing, so neither does.
    """
    return step.member not in _COLLAPSE_MEMBERS | _BOILERPLATE_MEMBERS


class _FocusIndex:
    """Readable focus changes of a log, indexed once per :func:`correlate` call.

    ``all_idx`` lists the sample indices where focus moved to a readable id and
    ``segment_at`` the last path segment of each; ``by_segment`` / ``by_suffix``
    list, per last path segment / DDIC suffix, the sample indices of those
    changes, each sorted ascending.
    """

    def __init__(self, log: MonitorLog) -> None:
        self.all_idx: list[int] = []
        self.segment_at: list[str] = []
        self.by_segment: dict[str, list[int]] = {}
        self.by_suffix: dict[str, list[int]] = {}
        for i, sample in enumerate(log.samples):
            if "focus_id" not in sample.changed:
                continue
            focus = sample.values.get("focus_id")
            if not isinstance(focus, str) or focus in _SENTINELS:
                continue
            seg = _last_segment(focus)
            self.all_idx.append(i)
            self.segment_at.append(seg)
            self.by_segment.setdefault(seg, []).append(i)
            sfx = _ddic_suffix(seg)
            if sfx is not None:
                self.by_suffix.setdefault(sfx, []).append(i)

    @staticmethod
    def after(events: list[int], idx: int) -> int:
        """How many of the sorted *events* lie after sample *idx*."""
        return len(events) - bisect_right(events, idx)


def _next_event(focus: _FocusIndex, seg: str, cursor: int) -> int | None:
    """The sample the greedy matcher would give a step on *seg* from *cursor*.

    Mirrors the matcher: the field's own focus changes first, then (when none is
    left at or after *cursor*) its DDIC suffix's, which survives layout shifts.
    """
    events = focus.by_segment.get(seg, [])
    pos = bisect_left(events, cursor)
    if pos < len(events):
        return events[pos]
    sfx = _ddic_suffix(seg)
    suffix_events = focus.by_suffix.get(sfx, []) if sfx is not None else []
    pos = bisect_left(suffix_events, cursor)
    return suffix_events[pos] if pos < len(suffix_events) else None


def _has_focus_events(focus: _FocusIndex, seg: str) -> bool:
    """Whether the log holds any focus change the replay could give *seg*."""
    sfx = _ddic_suffix(seg)
    return bool(focus.by_segment.get(seg)) or (sfx is not None and bool(focus.by_suffix.get(sfx)))


def _leaves_more_unmatched(
    focus: _FocusIndex, idx: int, target: int, segment: str, remaining: Sequence[tuple[str, bool]], start: int
) -> bool:
    """Whether taking *target* instead of collapsing onto *idx* starves a later step.

    Replays ``remaining[start:]`` (``(segment, consumes_an_event)`` per later
    step) greedily from both candidate cursors, in recording order, and counts
    the steps that find no event. Both replays are identical once their cursors
    meet, so the walk stops there.

    The replay is only exact for plain event-consuming steps on distinct fields.
    Whenever it meets anything else before the cursors meet it answers "yes",
    so the repeat collapses (flagged) instead of guessing:

    * a ``setFocus``/``caretPosition`` step (inherits an anchor, or does not);
    * a later step on a field already seen in the walk, including the repeated
      field itself (the real matcher's repeat/starvation rules apply to it);
    * a step the log holds no focus event for (a button press, a watched
      scroll, a modal), which other strategies anchor.
    """
    cursors = [idx, target]
    unmatched = [0, 0]
    seen = {segment}
    for k in range(start, len(remaining)):
        seg, consumes = remaining[k]
        if not consumes or seg in seen or not _has_focus_events(focus, seg):
            return True
        seen.add(seg)
        for which in (0, 1):
            nxt = _next_event(focus, seg, cursors[which])
            if nxt is None:
                unmatched[which] += 1
            else:
                cursors[which] = nxt
        if cursors[0] == cursors[1]:
            break
    return unmatched[1] > unmatched[0]


def _later_unclaimed_focus(
    focus: _FocusIndex,
    idx: int,
    segment: str,
    suffix: str | None,
    later_segments: Counter[str],
    later_suffixes: Counter[str],
    remaining: Sequence[tuple[str, bool]],
    start: int,
) -> int | None:
    """A later focus change for a repeated edit, or None to collapse.

    A repeat may skip over focus visits the recording never mentions (the
    person clicked another field and came back), but it must not starve a
    *later recorded step*. The target (the next change of this field after
    *idx*; matched by DDIC *suffix* when one is given) is refused when

    * later steps on the same field would be left without an event (a count), or
    * a field visited between *idx* and the target is edited by a later step
      and replaying the later steps from the target leaves more of them
      unmatched than replaying them from *idx* (order-aware; only run when such
      a visited field exists, so plain repeats stay cheap).
    """
    if suffix is not None:
        events = focus.by_suffix.get(suffix, [])
        later_same = later_suffixes[suffix]
    else:
        events = focus.by_segment.get(segment, [])
        later_same = later_segments[segment]
    if focus.after(events, idx) <= later_same:
        return None
    target = events[bisect_right(events, idx)]
    lo = bisect_right(focus.all_idx, idx)
    hi = bisect_left(focus.all_idx, target)
    for seg in set(focus.segment_at[lo:hi]):
        sfx = _ddic_suffix(seg)
        if later_segments[seg] or (sfx is not None and later_suffixes[sfx]):
            if _leaves_more_unmatched(focus, idx, target, segment, remaining, start):
                return None
            break
    return target


@dataclass
class _ModalBracket:
    """One open→close presence interval of a modal's ``wnd[N]:Text`` watch key."""

    key: str
    open_idx: int
    close_idx: int


def _modal_brackets(log: MonitorLog) -> list[_ModalBracket]:
    """Precompute every modal presence interval in the log.

    ``wnd[N]:Text`` (N ≥ 1) is present (a non-sentinel string) exactly while
    the modal N is open. A same-titled chained dialog is indistinguishable
    (the monitor's known limit) — brackets therefore span the whole
    present→absent interval, and sibling presses inside one modal share it.
    """
    text_keys = sorted({k for sample in log.samples for k in sample.values if re.fullmatch(r"wnd\[\d+\]:Text", k)})
    brackets: list[_ModalBracket] = []
    for key in text_keys:
        n = int(key[len("wnd[") : key.index("]")])
        if n == 0:
            continue  # the main window's title is the fingerprint signal, not a modal
        open_idx: int | None = None
        for i, sample in enumerate(log.samples):
            present = isinstance(sample.values.get(key), str) and sample.values[key] not in _SENTINELS
            if present and open_idx is None:
                open_idx = i
            elif not present and open_idx is not None:
                brackets.append(_ModalBracket(key, open_idx, i))
                open_idx = None
        if open_idx is not None:
            brackets.append(_ModalBracket(key, open_idx, len(log.samples) - 1))
    return brackets


def _modal_bracket_for(
    brackets: list[_ModalBracket], cursor: int, element_id: str, used: dict[str, _ModalBracket]
) -> _ModalBracket | None:
    """The first unconsumed bracket of the step's modal window not yet past the cursor.

    ``used`` maps ``wnd[N]`` to the bracket already taken for it: sibling steps
    inside one modal share the bracket (they cannot re-match a later modal),
    while a fresh ``wnd[N]`` after it closed takes the next bracket.
    """
    m = re.match(r"^wnd\[(\d+)\]", element_id)
    if not m or m.group(1) == "0":
        return None
    prefix = f"wnd[{m.group(1)}]"
    cached = used.get(prefix)
    # Sibling steps inside one modal share its bracket for as long as the cursor
    # has not reached its close sample (the first one where the modal is gone): a
    # ``wnd[0]`` step matched at or after that sample moves the cursor there, and
    # the next ``wnd[N]`` step then takes the next bracket.
    if cached is not None and cached.close_idx > cursor:
        return cached
    for bracket in brackets:
        # A bracket still open at the cursor is usable (an earlier step typed
        # into the dialog, and this one confirms it); only brackets already closed at the cursor skip.
        if bracket.key != f"{prefix}:Text" or bracket.close_idx <= cursor:
            continue
        if any(b.key == bracket.key and b.open_idx == bracket.open_idx for b in used.values()):
            continue  # this exact bracket was consumed by an earlier modal
        used[prefix] = bracket
        return bracket
    return None


def _screen_transition(log: MonitorLog, start: int) -> int | None:
    """The next sample after *start*-1 whose transaction/program/screen changed."""
    return _scan(log, start, lambda s: bool(s.changed & {"transaction", "program", "screen_number"}))


def _title_transition(log: MonitorLog, start: int) -> int | None:
    """The next main-window title change without a screen-geometry change —
    the #82 Finding-4 fingerprint for un-narratable button presses."""
    return _scan(
        log,
        start,
        lambda s: "wnd[0]:Text" in s.changed and not (s.changed & {"transaction", "program", "screen_number"}),
    )


def _watch_run_anchor(
    log: MonitorLog, cursor: int, step: RecordingStep, consumed: set[tuple[str, int]]
) -> tuple[int, str] | None:
    """The next unconsumed change of the watched property this assignment writes.

    Returns the sample index and the watch key. A change is consumed once a step
    took it, so two assignments to the *same* property bind to two changes, while
    a *different* property that changed in the same sample as the previous
    anchor is still available (the scan starts at the cursor, not after it).

    Only fires when the log actually carries the ``<element>:<Prop>`` key —
    a property nobody watched leaves no trace. The COM name is the Python
    member's snake_case re-camelized (``first_visible_row`` → ``FirstVisibleRow``).
    """
    key = _watch_key_of(step.element_id, step.member)
    if key is None or not any(key in s.values for s in log.samples):
        return None
    # Sample 0 is the baseline read, never a change.
    for i in range(max(cursor, 1), len(log.samples)):
        if key in log.samples[i].changed and (key, i) not in consumed:
            return i, key
    return None


def _watch_key_of(element_id: str, member: str) -> str | None:
    # Members arrive in both spellings: the recorder writes COM camelCase
    # (firstVisibleRow), sapsucker-style snake_case (first_visible_row)
    # normalizes to the same COM property name.
    if "_" in member:
        camel = "".join(p[:1].upper() + p[1:] for p in member.split("_"))
    else:
        camel = member[0].upper() + member[1:]
    return f"{element_id}:{camel}"


def _watch_value_of(sample: MonitorSample, step: RecordingStep) -> str | None:
    key = _watch_key_of(step.element_id, step.member)
    value = sample.values.get(key) if key else None
    # A failed read is not evidence of a mismatch — the monitor may simply
    # have sampled mid-transition; only a real differing value counts.
    if value is None or (isinstance(value, str) and value in _SENTINELS):
        return None
    return str(value)


def _next_keyboard_step(steps: list[RecordingStep], from_idx: int) -> RecordingStep | None:
    """The next recorded step, if it is a wnd[0] sendVKey (the Enter that
    submits what this okcd assignment typed)."""
    if from_idx + 1 >= len(steps):
        return None
    nxt = steps[from_idx + 1]
    if nxt.member == "sendVKey" and nxt.element_id.startswith("wnd[0]"):
        return nxt
    return None


def _sbar_at(log: MonitorLog, anchor: int) -> str | None:
    """The status-bar text in force at *anchor*: the last change of any
    ``sbar_text`` key at or before it, skipping sentinel values. None when the
    log has no status-bar keys or nothing was ever read."""
    sbar_keys = [k for k in log.samples[0].values if k.startswith("sbar_") and k.endswith("text")]
    for key in sbar_keys:
        last_good = None
        for sample in log.samples[: anchor + 1]:
            value = sample.values.get(key)
            if isinstance(value, str) and value not in _SENTINELS:
                last_good = value
        if last_good is not None:
            return last_good
    return None


def correlate(
    recording: Recording, log: MonitorLog, transcript: tuple[TranscriptEntry, ...] = ()
) -> CorrelatedTimeline:
    """Join a parsed recording, a monitor log and optional transcript entries.

    Every recorded step is timestamped by the first strategy that fits, in the
    order the plan doc fixes:

    1. watch-run — an assignment to a watched element property (ALV scrolling,
       which never moves focus) binds to the next change of that property's key;
    2. exact focus — the recorded id's last segment equals a changed sample's
       focus segment;
    3. DDIC-suffix — the field name after ``-`` survives the
       same-human-different-subtree variance, flagged ``layout-sensitive``;
    4. keyboard anchor, then modal bracketing — ``wnd[N]`` (N ≥ 1) presses get
       the modal's open→close window from the ``wnd[N]:Text`` watch key;
    5. screen/title fingerprints — ``press``/``sendVKey`` on ``wnd[0]`` bind to
       the next transaction/program/screen change, or to a main-window title
       change with stable screen geometry;
    6. boilerplate — ``resizeWorkingPane`` and friends are labelled rather
       than matched.

    Consecutive steps on the same element that cannot individually move state
    (``setFocus``/``caretPosition``) inherit the preceding matched step's
    window and are flagged ``sub-interval-collapse``.
    """
    steps_out: list[TimelineStep] = []
    cursor = 0
    prev_matched: TimelineStep | None = None
    used_modals: dict[str, _ModalBracket] = {}
    modal_brackets = _modal_brackets(log)
    focus_index = _FocusIndex(log)
    # Events still wanted by the steps *after* the current one; the current step
    # is removed from the counters at the top of its iteration.
    later_segments: Counter[str] = Counter()
    later_suffixes: Counter[str] = Counter()
    # (segment, consumes_an_event) of every non-boilerplate step, in order, and
    # how many of them the loop has reached (the current one included).
    walk = [
        (_last_segment(other.element_id), _consumes_event(other))
        for other in recording.steps
        if other.member not in _BOILERPLATE_MEMBERS
    ]
    walked = 0
    for other in recording.steps:
        if _consumes_event(other):
            seg = _last_segment(other.element_id)
            later_segments[seg] += 1
            if (sfx := _ddic_suffix(seg)) is not None:
                later_suffixes[sfx] += 1
    consumed_watch: set[tuple[str, int]] = set()

    for i, step in enumerate(recording.steps):
        segment = _last_segment(step.element_id)
        if step.member not in _BOILERPLATE_MEMBERS:
            walked += 1
        if _consumes_event(step):
            later_segments[segment] -= 1
            if (own_sfx := _ddic_suffix(segment)) is not None:
                later_suffixes[own_sfx] -= 1
        if step.member in _BOILERPLATE_MEMBERS:
            steps_out.append(_emit(step, "recorder-boilerplate", flags=()))
            continue

        # setFocus/caretPosition inherit the last step that took an anchor; an
        # unmatched row in between moved nothing, so it must not block that.
        collapse_from = _last_matched(steps_out)
        if (
            collapse_from is not None
            and step.member in _COLLAPSE_MEMBERS
            and step.element_id == collapse_from.element_id
            and collapse_from.t_start is not None
        ):
            steps_out.append(
                TimelineStep(
                    step.line_no,
                    step.element_id,
                    step.member,
                    step.args,
                    collapse_from.strategy,
                    collapse_from.confidence,
                    collapse_from.t_start,
                    collapse_from.t_end,
                    tuple(dict.fromkeys([*collapse_from.flags, "sub-interval-collapse"])),
                    collapse_from.sbar_text,
                    collapse_from.transcript,
                )
            )
            continue

        anchor: int | None = None
        strategy: str | None = None
        flags: list[str] = []

        # 1: watch-run — an assignment to an element whose watched property key
        # exists in the log (ALV scrolling never moves focus). Assignments try
        # this FIRST, before the focus strategies: a per-property change is the
        # more specific signal, and the focus move onto the element predates
        # every assignment after the first (the journey-5 scroll run).
        if step.args is not None and len(step.args) == 1:
            hit = _watch_run_anchor(log, cursor, step, consumed_watch)
            idx = hit[0] if hit is not None else None
            if (
                idx is None
                and prev_matched is not None
                and prev_matched.strategy == "watch-run"
                and prev_matched.element_id == step.element_id
                and prev_matched.member == step.member
            ):
                # More same-property assignments than the monitor caught
                # changes for — the documented collapse limit (journey-5: 6
                # assignments, 4 change samples). The leftovers share the last
                # observed window, flagged, rather than reading as unmatched.
                # ``value-mismatch`` is per assignment: recompute it against the
                # shared sample instead of inheriting the previous step's.
                inherited = [f for f in prev_matched.flags if f != "value-mismatch"]
                if _watch_value_of(log.samples[cursor], step) not in (None, step.args[0]):
                    inherited.append("value-mismatch")
                steps_out.append(
                    TimelineStep(
                        step.line_no,
                        step.element_id,
                        step.member,
                        step.args,
                        "watch-run",
                        "high",
                        prev_matched.t_start,
                        prev_matched.t_end,
                        tuple(dict.fromkeys([*inherited, "sub-interval-collapse"])),
                        prev_matched.sbar_text,
                    )
                )
                prev_matched = steps_out[-1]
                continue
            if hit is not None:
                idx, watch_key = hit
                consumed_watch.add((watch_key, idx))
                sample = log.samples[idx]
                anchor, strategy = idx, "watch-run"
                if _watch_value_of(sample, step) not in (None, step.args[0]):
                    flags.append("value-mismatch")
                if idx == cursor and _last_matched(steps_out) is not None:
                    # Another step already took this sample (typically a different
                    # watched property that changed in the same sample): one
                    # observed instant, two steps.
                    flags.append("sub-interval-collapse")

        # 2: exact focus match
        if anchor is None:
            idx = _scan(log, cursor, _focus_on_segment(segment))
            if idx is not None and _is_repeat_anchor(_last_matched(steps_out), segment):
                # A consecutive step on the same field matched the same sample the
                # previous step already took: look for a *later* occurrence before
                # accepting the shared anchor (two edits of one field bind to two
                # focus changes when the log has both).
                later = _later_unclaimed_focus(
                    focus_index, idx, segment, None, later_segments, later_suffixes, walk, walked
                )
                if later is not None:
                    idx = later
                else:
                    flags.append("sub-interval-collapse")  # two edits, one observed event
            if idx is not None:
                anchor, strategy = idx, "exact-focus"

        # 3: DDIC-field-suffix match
        if anchor is None and "-" in segment:
            suffix = _ddic_suffix(segment)
            idx = _scan(log, cursor, _focus_on_suffix(suffix))
            if idx is not None and _is_repeat_anchor(_last_matched(steps_out), segment, suffix):
                later = _later_unclaimed_focus(
                    focus_index, idx, segment, suffix, later_segments, later_suffixes, walk, walked
                )
                if later is not None:
                    idx = later
                else:
                    flags.append("sub-interval-collapse")
            if idx is not None:
                anchor, strategy = idx, "ddic-suffix"
                flags.append("layout-sensitive")
                ambiguous = _scan(
                    log,
                    idx + 1,
                    _focus_on_suffix(suffix),
                )
                if ambiguous is not None:
                    flags.append("suffix-ambiguous")

        # 4: keyboard-anchor — an okcd assignment followed by sendVKey: typing
        # into an already-focused command line produces no focus change, so the
        # pair binds to the screen transition the sendVKey causes (observed
        # live: /nse16n from a session parked on the command line). The sendVKey
        # step itself inherits this anchor: it is the second half of the same
        # action, and both steps share one transition sample.
        if (
            anchor is None
            and prev_matched is not None
            and prev_matched.strategy == "fingerprint-screen"
            and "keyboard-anchor" in prev_matched.flags
            and step.member == "sendVKey"
            and step.element_id.startswith("wnd[0]")
            and prev_matched.element_id.endswith("/tbar[0]/okcd")
        ):
            steps_out.append(
                _emit(
                    step,
                    "fingerprint-screen",
                    t_start=prev_matched.t_start,
                    flags=("keyboard-anchor", "sub-interval-collapse"),
                    sbar=prev_matched.sbar_text,
                )
            )
            prev_matched = steps_out[-1]
            continue
        if (
            anchor is None
            and step.element_id.endswith("/tbar[0]/okcd")
            and step.args is not None
            and _next_keyboard_step(recording.steps, i) is not None
        ):
            idx = _screen_transition(log, cursor + 1)
            if idx is not None:
                anchor, strategy = idx, "fingerprint-screen"
                flags.append("keyboard-anchor")

        # 5: modal bracketing — wnd[N] (N >= 1) steps take the modal's
        # open->close window; each bracket is consumable once.
        t_end: int | None = None
        if anchor is None:
            bracket = _modal_bracket_for(modal_brackets, cursor, step.element_id, used_modals)
            if bracket is not None:
                anchor = max(bracket.open_idx, cursor)
                strategy = "modal-bracket"
                t_end = bracket.close_idx

        # 6: screen/title fingerprints for press/sendVKey/select on wnd[0].
        if anchor is None and step.member in _SCREEN_MEMBERS and re.match(r"^wnd\[0\]", step.element_id):
            # The FIRST subsequent observable event wins — a press's effect is
            # whatever changes next (screen geometry, or title with focus move
            # for #82 Finding-4 buttons); taking a later transition would skip
            # over the steps the press precedes.
            screen_idx = _screen_transition(log, cursor + 1)
            title_idx = _title_transition(log, cursor + 1)
            if screen_idx is not None and (title_idx is None or screen_idx <= title_idx):
                anchor, strategy = screen_idx, "fingerprint-screen"
            elif title_idx is not None:
                anchor, strategy = title_idx, "fingerprint-title"
            elif (
                prev_matched is not None
                and prev_matched.strategy == "fingerprint-screen"
                and prev_matched.member == step.member
                and prev_matched.element_id == step.element_id
            ):
                # Two consecutive same-element presses collapsed into one
                # transition (journey-5's sendVKey 0 / sendVKey 8): inherit
                # rather than report unmatched — the log genuinely holds one
                # event for two actions, and the collapse flag says so.
                steps_out.append(
                    _emit(
                        step,
                        "fingerprint-screen",
                        t_start=prev_matched.t_start,
                        flags=("sub-interval-collapse",),
                        sbar=prev_matched.sbar_text,
                    )
                )
                prev_matched = steps_out[-1]
                continue

        if anchor is not None:
            assert strategy is not None
            sample = log.samples[anchor]
            t_end_elapsed = log.samples[t_end].elapsed if t_end is not None else None
            steps_out.append(
                _emit(
                    step,
                    strategy,
                    t_start=sample.elapsed,
                    t_end=t_end_elapsed,
                    flags=tuple(flags),
                    sbar=_sbar_at(log, anchor),
                )
            )
            # Monotonic cursor: a later step may share this anchor (sub-interval
            # actions collapse) but never lands before it. Sharing is what
            # makes two edits of one field two steps on one window; scanning
            # from the anchor itself (not anchor + 1) is what allows that.
            cursor = anchor
            prev_matched = steps_out[-1]
            continue

        steps_out.append(_emit(step, "unmatched"))
        prev_matched = steps_out[-1]

    steps_out = _attach_transcripts(steps_out, transcript, log.recorder_skew or 0.0)

    return CorrelatedTimeline(
        steps=steps_out,
        recording_path=recording.path,
        recorder_skew=log.recorder_skew,
        clock_origin_assumed=log.clock_origin_assumed,
        strategy_counts=dict(Counter(s.strategy for s in steps_out)),
    )


#: A transcript excerpt attaches when it overlaps the step's window widened by
#: this much: a person starts talking about a step slightly before/after the
#: screen reacts. Steps without a window use ± the same slack around t_start.
_TRANSCRIPT_SLACK = 2.0


def _attach_transcripts(
    steps_out: list[TimelineStep], transcript: tuple[TranscriptEntry, ...], skew: float = 0.0
) -> list[TimelineStep]:
    """Attach verbatim excerpts intersecting each step's widened window.

    Rebuilding the frozen dataclasses rather than mutating: the timeline is a
    value, and a caller holding a step must not see it grow an excerpt.

    Step times are in sampler-origin seconds; an SRT is taken to be relative to
    the recorder start, which a ``--record`` log measured as *skew* seconds
    later, so cues are shifted by it. Unverified assumption: that the narration
    recording starts together with the recorder.
    """
    if not transcript:
        return steps_out
    rebuilt: list[TimelineStep] = []
    for step in steps_out:
        if step.t_start is None:
            excerpts: tuple[str, ...] = ()
        else:
            lo = step.t_start - _TRANSCRIPT_SLACK
            hi = (step.t_end if step.t_end is not None else step.t_start) + _TRANSCRIPT_SLACK
            excerpts = tuple(
                entry.text for entry in transcript if entry.t_start + skew <= hi and entry.t_end + skew >= lo
            )
        rebuilt.append(TimelineStep(**{**step.__dict__, "transcript": excerpts}))
    return rebuilt


def _emit(
    step: RecordingStep,
    strategy: str,
    *,
    t_start: float | None = None,
    t_end: float | None = None,
    flags: tuple[str, ...] = (),
    sbar: str | None = None,
) -> TimelineStep:
    return TimelineStep(
        line_no=step.line_no,
        element_id=step.element_id,
        member=step.member,
        args=step.args,
        strategy=strategy,
        confidence=_STRATEGY_CONFIDENCE[strategy],
        t_start=t_start,
        t_end=t_end,
        flags=flags,
        sbar_text=sbar,
    )
