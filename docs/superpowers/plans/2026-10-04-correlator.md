# Correlator (recording→artefact phase 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sapsucker.correlate` — join a recorded `.vbs` (from `_recording`), a monitor JSONL and an optional transcript into one timestamped timeline (issue #126, phase 5 of the plan in `docs/superpowers/specs/2026-10-03-recording-to-skill-pipeline-design.md`).

**Architecture:** A pure library module `src/sapsucker/_correlate.py` (parser + matcher + timeline model) and a small `sapsucker-correlate` CLI behind the existing entry-point shim pattern. The matcher walks recorded steps in order, timestamping each by five strategies in priority order (exact focus → DDIC-suffix → modal bracket → screen/title fingerprint → watch-run), with a monotonic cursor so steps cannot match backwards. JSONL + markdown output. No COM involved — fully testable in CI.

**Tech Stack:** Python 3.11+, stdlib only in the library module; `typer` in the CLI (already a dependency of the `cli` extra). pydantic for the timeline record (consistent with `monitor.Sample`). pytest, ruff, mypy --strict.

**Verified evidence this is buildable (2026-10-04):** A prototype of this matcher ran against the real journey-6 JSONL (4134 samples, recovered from git history commit `3581a44^:journey6_bp_timing.jsonl`) paired with the committed `docs/spike/journey3_bp.vbs`: **16/17 statements timestamped, 17 strategies assigned** — 10 exact-focus, 3 ddic-suffix, 2 modal-bracket, 1 fingerprint-screen, 1 fingerprint-title, only `resizeWorkingPane` labelled `recorder-boilerplate`. This reproduces the #126 prototype score (17/18 counting the boilerplate as handled). The committed `journey5_bp.vbs` × `journey5_timing.jsonl` pair exercises the watch-run strategy (`firstVisibleRow` never moves focus).

**Where evidence files live during development:** `<scratch>\journey6_bp_timing.jsonl` (scratch, git-ignored by the repo-wide `*.jsonl` rule — do NOT commit it; the cross-journey unit tests use small synthetic fixtures instead).

---

## File structure

- **Create:** `src/sapsucker/_correlate.py` — data model (`TimelineStep`, `TranscriptEntry`, `CorrelatedTimeline`), JSONL/monitor-log loading (both schema v2 `elapsed_s` and v3+ ISO-durations + `record_type: header`), the matcher, markdown/JSONL renderers.
- **Create:** `src/sapsucker/_correlate_entry.py` — console-script shim, same pattern as `_monitor_entry.py` (explains the `[cli]` extra when typer is missing).
- **Create:** `src/sapsucker/correlate_cli.py` — typer CLI: `sapsucker-correlate RECORDING JSONL [-t TRANSCRIPT] [-o OUT.jsonl] [-m OUT.md]`.
- **Modify:** `pyproject.toml` — add `sapsucker-correlate = "sapsucker._correlate_entry:main"` under `[project.scripts]`.
- **Modify:** `README.md` — a short "Correlating a recording with its log" subsection after the monitor section.
- **Create:** `unittests/test_correlate.py` — unit tests (pure, no COM).

Design notes locked in from the grounding run:

1. **Cursor semantics.** One monotonic cursor over sample indices. Exact/suffix/fingerprint matches set `cursor = anchor` (a later step may share the same sample — sub-interval collapse). Modal brackets set `cursor` to the bracket's *open* index, and each modal bracket is consumable only once (its `wnd[N]:Text` key is then blacklisted) so sibling presses inside one modal share the window instead of the second press re-matching a later modal.
2. **Modal membership.** A step whose element id starts with `wnd[N]` (N ≥ 1) matches the first *not-yet-consumed* modal bracket of the same `wnd[N]` whose open index ≥ cursor. Window = `[open.elapsed, close.elapsed]`.
3. **Watch-run strategy.** A step on element E setting a watched property P (`firstVisibleRow = 8` — assignments whose element+property key is in the log) matches the next sample in which key `E:P` changed. This timestamps ALV scrolling, which never moves focus. The *value* is compared against the sample's value where readable: a mismatch adds the `value-mismatch` flag but still matches (the monitor may have missed the intermediate value — known sampling limit).
4. **Fingerprints** apply only to `press`/`sendVKey`/`select` on `wnd[0]…`: scan forward for the next sample with (a) `transaction`/`program`/`screen_number` change → `fingerprint-screen`, or (b) a `wnd[0]:Text` title change *without* a screen-geometry change → `fingerprint-title`. Confidence `medium` for both.
5. **Sub-interval collapse.** `setFocus`/`caretPosition` steps whose element matches the immediately preceding *matched* step's element inherit that step's result and get the `sub-interval-collapse` flag (not doubled).
6. **Boilerplate table.** `resizeWorkingPane`, `maximize`, `sendCommand("")`-style no-ops: strategy `recorder-boilerplate`, confidence `high`, no timestamp, never consumes the cursor.
7. **Transcript.** Input is a normalized `[(t_start, t_end, text)]` stream; the CLI accepts SRT only (whisper JSON is out of scope until someone needs it — YAGNI). Entries intersect a step's window (or ±2 s around a single-point timestamp) and are attached verbatim.
8. **Output fields** per step: `line_no`, `element_id`, `member`, `args`, `strategy`, `confidence`, `t_start`/`t_end` (null when none), `flags`, `sbar_text` (last status-bar text at or before the anchor — null when no anchor), `transcript` excerpts. Confidence: exact-focus `high`, ddic-suffix `medium`(+`layout-sensitive`), watch-run `high`, fingerprints `medium`, modal-bracket `medium`, boilerplate `high`.
9. **Clock alignment.** v2 logs have no header; correlate a v2 log by assuming monitor origin ≈ recording start (flagged `clock-origin-assumed` in the timeline metadata). v3+ `--record` logs carry the header: subtract `recorder_skew` from every step timestamp (recording started `skew` after the sampler origin) and record the alignment in the metadata.
10. **Path/scoping guard:** matching scans only *forward* from the cursor, never backwards; a step that would match before the previous step's anchor is treated as sub-interval (collapsed) rather than re-scanning from 0.

---

### Task 1: Monitor-log loader (schema v2 + v3+)

**Files:**
- Create: `src/sapsucker/_correlate.py` (loader part)
- Test: `unittests/test_correlate.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for sapsucker.correlate (recording → artefact phase 5, #126)."""

import json

import pytest

from sapsucker._correlate import MonitorLog, load_monitor_log


class TestLoadMonitorLog:
    def test_v2_flat_schema(self):
        lines = [
            json.dumps({"seq": 0, "elapsed_s": 0.015, "changed": [], "transaction": "SE16N", "focus_id": "<unreadable>", "wnd[0]:Text": "T"}),
            json.dumps({"seq": 1, "elapsed_s": 0.218, "changed": ["focus_id"], "transaction": "SE16N", "focus_id": "/app/con[0]/ses[0]/wnd[0]/usr/txtF", "wnd[0]:Text": "T"}),
        ]
        log = load_monitor_log(lines)
        assert isinstance(log, MonitorLog)
        assert log.samples[1].elapsed == pytest.approx(0.218)
        assert log.samples[1].changed == {"focus_id"}
        assert log.samples[1].values["focus_id"].endswith("txtF")
        assert log.recorder_skew is None
        assert log.clock_origin_assumed is True

    def test_v3_header_and_iso_durations(self):
        lines = [
            json.dumps({"schema_version": 3, "record_type": "header", "origin_at": "2026-08-26T14:17:28+02:00", "recording_file": "JOURNEY6.VBS", "recorder_skew": 0.42}),
            json.dumps({"schema_version": 3, "seq": 0, "at": "2026-08-26T14:17:28.354+02:00", "elapsed": "PT0.015S", "changed": [], "transaction": "SE16N", "focus_id": "x"}),
        ]
        log = load_monitor_log(lines)
        assert log.recorder_skew == pytest.approx(0.42)
        assert log.clock_origin_assumed is False
        assert log.samples[0].elapsed == pytest.approx(0.015)

    def test_non_json_line_raises(self):
        with pytest.raises(ValueError, match="line 2"):
            load_monitor_log(["{}", "not json"])
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest unittests/test_correlate.py -x -q` → ImportError.

- [ ] **Step 3: Implement** in `src/sapsucker/_correlate.py`:

```python
"""Correlate a recorded .vbs with a monitor JSONL and an optional transcript.

Phase 5 of the recording→artefact plan (docs/superpowers/specs/), issue #126.
...
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_UNPACKED_KEYS = frozenset({"schema_version", "seq", "at", "elapsed", "elapsed_s", "changed",
                            "gap_since_change", "gap_since_change_s", "record_type",
                            "origin_at", "recording_file", "recorder_skew"})
_ISO_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+(?:\.\d+)?)M)?(\d+(?:\.\d+)?)S$")


@dataclass(frozen=True)
class MonitorSample:
    seq: int
    elapsed: float  # seconds since the sampler origin
    changed: frozenset[str]
    values: dict  # key -> JSON value (str/int/bool/None)


@dataclass(frozen=True)
class MonitorLog:
    samples: list[MonitorSample]
    recorder_skew: float | None = None
    clock_origin_assumed: bool = True


def _parse_elapsed(d: dict) -> float:
    if "elapsed_s" in d:
        return float(d["elapsed_s"])
    m = _ISO_DURATION.match(d.get("elapsed", ""))
    if not m:
        raise ValueError(f"sample {d.get('seq')}: no usable elapsed field ({d.get('elapsed')!r})")
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + float(mi or 0) * 60 + float(s or 0)


def load_monitor_log(lines: list[str]) -> MonitorLog:
    """Parse monitor JSONL lines (schema v2 flat or v3+ with header record)."""
    samples: list[MonitorSample] = []
    recorder_skew: float | None = None
    for line_no, raw in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: not JSON: {raw[:80]!r}") from exc
        if not isinstance(d, dict):
            raise ValueError(f"line {line_no}: expected a JSON object")
        if d.get("record_type") == "header":
            recorder_skew = d.get("recorder_skew")
            continue
        samples.append(MonitorSample(
            seq=int(d.get("seq", len(samples))),
            elapsed=_parse_elapsed(d),
            changed=frozenset(d.get("changed") or ()),
            values={k: v for k, v in d.items() if k not in _UNPACKED_KEYS},
        ))
    if not samples:
        raise ValueError("no samples in monitor log")
    return MonitorLog(samples=samples, recorder_skew=recorder_skew,
                      clock_origin_assumed=recorder_skew is None)
```

- [ ] **Step 4: Run tests** — pass. Commit: `feat(correlate): monitor-log loader (v2 flat + v3 header)`.

---

### Task 2: Core matcher — exact focus, suffix, boilerplate, collapse

**Files:**
- Modify: `src/sapsucker/_correlate.py`
- Test: `unittests/test_correlate.py`

- [ ] **Step 1: Failing tests.** Use a tiny synthetic recording via `Recording.parse` (the parser already exists) + `load_monitor_log`:

```python
from sapsucker._correlate import correlate
from sapsucker._recording import Recording

REC = 'session.findById("wnd[0]").resizeWorkingPane 152,33,false\n' \
      'session.findById("wnd[0]/tbar[0]/okcd").text = "/nse16n"\n' \
      'session.findById("wnd[0]/usr/ctxtGD-TAB").text = "T000"\n' \
      'session.findById("wnd[0]/usr/ctxtGD-TAB").setFocus\n' \
      'session.findById("wnd[0]/usr/ctxtGD-TAB").caretPosition = 4\n'

def _log(*rows):  # rows: (elapsed, changed, values)
    return load_monitor_log([
        json.dumps({"seq": i, "elapsed_s": e, "changed": list(c), **v})
        for i, (e, c, v) in enumerate(rows)
    ])

class TestMatcherBasics:
    def test_exact_focus_and_boilerplate_and_collapse(self):
        log = _log(
            (0.0, [], {"transaction": "SESSION_MANAGER", "focus_id": "/app/con[0]/ses[0]/wnd[0]/shellcont/shell"}),
            (1.0, ["focus_id"], {"transaction": "SESSION_MANAGER", "focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/okcd"}),
            (2.0, ["focus_id"], {"transaction": "SE16N", "focus_id": "/app/con[0]/ses[0]/wnd[0]/usr/ctxtGD-TAB"}),
        )
        tl = correlate(Recording.parse(REC), log)
        assert tl.steps[0].strategy == "recorder-boilerplate"
        assert tl.steps[0].t_start is None
        assert tl.steps[1].strategy == "exact-focus"
        assert tl.steps[1].t_start == pytest.approx(1.0)
        assert tl.steps[2].strategy == "exact-focus"
        assert tl.steps[2].t_start == pytest.approx(2.0)
        # setFocus/caretPosition inherit the preceding text assignment's window
        for s in tl.steps[3:5]:
            assert s.strategy == "exact-focus"
            assert s.flags == ("sub-interval-collapse",)
            assert s.t_start == pytest.approx(2.0)

    def test_ddic_suffix_flags_layout_sensitive(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "x"\n')
        log = _log(
            (0.0, [], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/usr/txtOTHER"}),
            (1.0, ["focus_id"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/usr/txtSZA7_D0400-TEL_NUMBER"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "ddic-suffix"
        assert "layout-sensitive" in tl.steps[0].flags
        assert tl.steps[0].t_start == pytest.approx(1.0)

    def test_unmatched_step(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtNOPE").text = "x"\n')
        log = _log((0.0, [], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/usr/txtOTHER"}))
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "unmatched"
        assert tl.steps[0].t_start is None
```

- [ ] **Step 2: Verify failure** (ImportError: `correlate`).
- [ ] **Step 3: Implement** the model + matcher skeleton:

```python
@dataclass(frozen=True)
class TranscriptEntry:
    t_start: float
    t_end: float
    text: str


@dataclass(frozen=True)
class TimelineStep:
    line_no: int
    element_id: str
    member: str
    args: tuple[str, ...] | None
    strategy: str
    confidence: str  # high | medium | low
    t_start: float | None = None
    t_end: float | None = None
    flags: tuple[str, ...] = ()
    sbar_text: str | None = None
    transcript: tuple[str, ...] = ()


@dataclass(frozen=True)
class CorrelatedTimeline:
    steps: list[TimelineStep]
    recording_path: str | None = None
    recorder_skew: float | None = None
    clock_origin_assumed: bool = True
    strategy_counts: dict[str, int] = field(default_factory=dict)

    def to_jsonl(self) -> str: ...   # one step per line, args as list, nulls kept
    def to_markdown(self) -> str: ...  # task 5


_PURE = frozenset()  # placeholder; see module for the boilerplate table
_BOILERPLATE_MEMBERS = frozenset({"resizeWorkingPane", "maximize"})


def _last_segment(element_id: str) -> str:
    return element_id.rsplit("/", 1)[-1]


def _ddic_suffix(segment: str) -> str | None:
    return segment.rsplit("-", 1)[-1] if "-" in segment else None


def _scan(log, cursor: int, pred, start_offset: int = 0) -> int | None:
    i = cursor + start_offset
    while i < len(log.samples):
        if pred(log.samples[i]):
            return i
        i += 1
    return None


_CONFIDENCE = {"exact-focus": "high", "ddic-suffix": "medium", "watch-run": "high",
               "fingerprint-screen": "medium", "fingerprint-title": "medium",
               "modal-bracket": "medium", "recorder-boilerplate": "high"}


def correlate(recording, log, transcript=()):
    """Return the merged timeline (see README / plan doc)."""
    steps_out: list[TimelineStep] = []
    cursor = 0
    prev_matched: TimelineStep | None = None
    prev_matched_sample_elapsed: float | None = None
    for step in recording.steps:
        seg = _last_segment(step.element_id)
        flags: list[str] = []
        if step.member in _BOILERPLATE_MEMBERS:
            steps_out.append(TimelineStep(step.line_no, step.element_id, step.member, step.args,
                                          "recorder-boilerplate", "high"))
            continue
        if (prev_matched is not None and step.member in ("setFocus", "caretPosition")
                and step.element_id == prev_matched.element_id and prev_matched.t_start is not None):
            steps_out.append(TimelineStep(step.line_no, step.element_id, step.member, step.args,
                                          prev_matched.strategy, prev_matched.confidence,
                                          prev_matched.t_start, prev_matched.t_end,
                                          tuple(dict.fromkeys([*prev_matched.flags, "sub-interval-collapse"])),
                                          prev_matched.sbar_text))
            continue
        anchor = None
        strategy = None
        # 1: exact focus
        idx = _scan(log, cursor, lambda s: "focus_id" in s.changed
                    and s.values.get("focus_id") not in ("<unreadable>", "<absent>")
                    and _last_segment(str(s.values.get("focus_id"))) == seg)
        if idx is not None:
            anchor, strategy = idx, "exact-focus"
        # 2: ddic suffix
        if anchor is None and "-" in seg:
            suffix = _ddic_suffix(seg)
            idx = _scan(log, cursor, lambda s: "focus_id" in s.changed
                        and s.values.get("focus_id") not in ("<unreadable>", "<absent>")
                        and _ddic_suffix(_last_segment(str(s.values.get("focus_id")))) == suffix)
            if idx is not None:
                anchor, strategy = idx, "ddic-suffix"
                flags.append("layout-sensitive")
        if anchor is not None:
            sample = log.samples[anchor]
            steps_out.append(TimelineStep(step.line_no, step.element_id, step.member, step.args,
                                          strategy, _CONFIDENCE[strategy],
                                          sample.elapsed, None, tuple(flags),
                                          _sbar_at(log, anchor)))
            cursor = anchor
            prev_matched = steps_out[-1]
        else:
            steps_out.append(TimelineStep(step.line_no, step.element_id, step.member, step.args,
                                          "unmatched", "low"))
            prev_matched = steps_out[-1] if prev_matched is None else prev_matched
    return CorrelatedTimeline(steps_out, recording.path, log.recorder_skew,
                              log.clock_origin_assumed,
                              dict(Counter(s.strategy for s in steps_out)))
```

plus `_sbar_at(log, anchor)`: walk `log.samples[anchor]` back to the last sample where any `sbar_text` key changed and is not a sentinel; return its text (None if none / no sbar keys in the log).

- [ ] **Step 4: Run tests** — pass. Commit: `feat(correlate): matcher core (exact focus, ddic-suffix, collapse)`.

---

### Task 3: Modal brackets + fingerprints + watch-run

**Files:**
- Modify: `src/sapsucker/_correlate.py`
- Test: `unittests/test_correlate.py`

- [ ] **Step 1: Failing tests**

```python
class TestModalBracket:
    def test_two_presses_share_one_modal_window(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/tbar[0]/btn[11]").press\n'
            'session.findById("wnd[1]/usr/btnBUTTON_1").press\n'
            'session.findById("wnd[1]/usr/btnBUTTON_2").press\n')
        log = _log(
            (1.0, ["focus_id"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/btn[11]", "wnd[1]:Text": "<absent>"}),
            (2.0, ["wnd[1]:Text"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/btn[11]", "wnd[1]:Text": "Warnung"}),
            (4.0, ["wnd[1]:Text"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/usr", "wnd[1]:Text": "<absent>"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[1].strategy == "modal-bracket"
        assert tl.steps[1].t_start == pytest.approx(2.0)
        assert tl.steps[1].t_end == pytest.approx(4.0)
        # sibling press shares the same consumed bracket
        assert tl.steps[2].strategy == "modal-bracket"
        assert tl.steps[2].t_start == pytest.approx(2.0)

    def test_bracket_not_reused_after_close(self):
        rec = Recording.parse(
            'session.findById("wnd[1]/usr/btnA").press\n'
            'session.findById("wnd[1]/usr/btnB").press\n')
        log = _log(
            (1.0, [], {"wnd[1]:Text": "<absent>"}),
            (2.0, ["wnd[1]:Text"], {"wnd[1]:Text": "One"}),
            (3.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>"}),
            (4.0, [], {"wnd[1]:Text": "<absent>"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "modal-bracket"
        assert tl.steps[1].strategy == "unmatched"


class TestFingerprints:
    def test_send_vkey_binds_to_transaction_change(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/tbar[0]/okcd").text = "/nbp"\n'
            'session.findById("wnd[0]").sendVKey 0\n')
        log = _log(
            (1.0, ["focus_id"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/okcd", "transaction": "SESSION_MANAGER", "screen_number": 100}),
            (2.5, ["transaction", "program", "screen_number"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/okcd", "transaction": "BP", "screen_number": 3000}),
        )
        tl = correlate(rec, log)
        assert tl.steps[1].strategy == "fingerprint-screen"
        assert tl.steps[1].t_start == pytest.approx(2.5)

    def test_press_binds_to_title_change(self):
        rec = Recording.parse('session.findById("wnd[0]/tbar[0]/btn[11]").press\n')
        log = _log(
            (1.0, ["focus_id"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/btn[11]", "wnd[0]:Text": "Person anlegen", "screen_number": 3000}),
            (2.0, ["wnd[0]:Text"], {"focus_id": "/app/con[0]/ses[0]/wnd[0]/tbar[0]/btn[11]", "wnd[0]:Text": "Person anzeigen: 3961", "screen_number": 3000}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "fingerprint-title"
        assert tl.steps[0].t_start == pytest.approx(2.0)


class TestWatchRun:
    def test_first_visible_row_assignment_uses_watch_key(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 1\n'
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8\n')
        log = _log(
            (1.0, [], {"wnd[0]/shellcont/shell:FirstVisibleRow": "0"}),
            (2.0, ["wnd[0]/shellcont/shell:FirstVisibleRow"], {"wnd[0]/shellcont/shell:FirstVisibleRow": "1"}),
            (5.0, ["wnd[0]/shellcont/shell:FirstVisibleRow"], {"wnd[0]/shellcont/shell:FirstVisibleRow": "8"}),
            (6.0, ["wnd[0]/shellcont/shell:FirstVisibleRow"], {"wnd[0]/shellcont/shell:FirstVisibleRow": "55"}),  # monitor caught an extra scroll
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "watch-run"
        assert tl.steps[0].t_start == pytest.approx(2.0)
        assert tl.steps[1].strategy == "watch-run"
        assert tl.steps[1].t_start == pytest.approx(5.0)

    def test_value_mismatch_flagged_not_failed(self):
        rec = Recording.parse('session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 470\n')
        log = _log(
            (1.0, [], {"wnd[0]/shellcont/shell:FirstVisibleRow": "0"}),
            (2.0, ["wnd[0]/shellcont/shell:FirstVisibleRow"], {"wnd[0]/shellcont/shell:FirstVisibleRow": "144"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "watch-run"
        assert "value-mismatch" in tl.steps[0].flags
        assert tl.steps[0].t_start == pytest.approx(2.0)
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement.**

Modal: precompute brackets per `wnd[N]:Text` key (N ≥ 1) by scanning the full log for present→absent transitions (present = value not in sentinels). A step on `wnd[N]/…` (N ≥ 1) takes the first unconsumed bracket with `open >= cursor`; consumes it; window `[open.elapsed, close.elapsed]`; `cursor = open` (so the next sibling, whose cursor is still ≤ open, finds the same bracket — consumed, so it cannot re-match a *later* bracket by accident? No: it must find the same *window*, so a consumed bracket is reusable by steps whose element shares the same `wnd[N]` prefix **only while the step's previous matched anchor ≤ close** — implement as: brackets store `consumed_by: list[step]`; a step may attach to an already-consumed bracket only if `cursor <= bracket.open`); cursor then moves to `bracket.close`. Re-read this rule against the two modal tests before coding.

Fingerprint: `press`/`sendVKey`/`select` on `wnd[0]/…` and no focus/other match: scan from `cursor + 1` for first sample with any of `transaction|program|screen_number` in `changed` → `fingerprint-screen`; else the first sample with a `wnd[0]:Text` change (and none of the three) → `fingerprint-title`. Do not advance the cursor past the fingerprint sample (a following field-fill step matches focus changes after it — set `cursor = anchor`).

Watch-run: applies when the step is an assignment (`args` length 1) whose `<element_id>:<CamelCase prop>` key exists in the log's keys. Property name from member: `firstVisibleRow` → `FirstVisibleRow` via the casing table in `_types.py`? — no: build the key by camelCasing the member (`first_visible_row`-style is not stored; simply capitalize the first letter: `firstVisibleRow` → `FirstVisibleRow`, `text` → `Text`, `key` → `Key`) and require the full key to be present in ≥ 1 sample's values. Match the next sample (from cursor+1) where that key is in `changed`; compare sample value to the recorded arg (string-normalized); mismatch → flag. `cursor = anchor`.

Order of strategies in the final matcher: boilerplate → collapse-inherit → exact-focus → ddic-suffix → watch-run → modal-bracket → fingerprint-screen/title → unmatched.

- [ ] **Step 4: Run full test file.** Commit: `feat(correlate): modal brackets, screen/title fingerprints, watch-run`.

---

### Task 4: Cross-journey acceptance tests (the 17/18 evidence, synthetic-free)

**Files:**
- Test: `unittests/test_correlate.py`

The real journey-6 JSONL stays in the temp scratch dir (never committed). Write the two assertions so they run **only if the scratch file exists** (`pytest.skipif not os.path.exists(...)`) — they are a local acceptance gate, documented as such; CI runs the synthetic suite. (AGENTS.md: never write "verified" on anything CI cannot reach — the scratch-gated test documents exactly what was and was not verified.)

- [ ] **Step 1: Write the acceptance test**

```python
J6 = Path(r"<scratch>\journey6_bp_timing.jsonl")

@pytest.mark.skipif(not J6.exists(), reason="journey-6 JSONL recovered from git history lives only in this machine's temp dir; CI runs the synthetic suite")
class TestJourney6Acceptance:
    def test_cross_journey_score(self):
        rec = Recording.load("docs/spike/journey3_bp.vbs")
        log = load_monitor_log(J6.read_text(encoding="utf-8").splitlines())
        tl = correlate(rec, log)
        counts = Counter(s.strategy for s in tl.steps)
        assert counts["exact-focus"] == 10
        assert counts["ddic-suffix"] == 3
        assert counts["modal-bracket"] == 2
        assert counts["fingerprint-screen"] == 1
        assert counts["fingerprint-title"] == 1
        assert counts["recorder-boilerplate"] == 1
        assert counts["unmatched"] == 0
```

- [ ] **Step 2: Run locally** — must pass (this is the grounding evidence, already reproduced by the prototype).
- [ ] **Step 3: Run committed-pair test** (journey5, fully committed, runs in CI):

```python
class TestJourney5CommittedPair:
    """journey5_bp.vbs + journey5_timing.jsonl are both committed (phase 0).

    firstVisibleRow never moves focus, so all scroll assignments must come out
    watch-run; the SE16N navigation steps exact/fingerprint.
    """
    def test_scrolls_are_watch_run(self):
        rec = Recording.load("docs/spike/journey5_bp.vbs")
        log = load_monitor_log(Path("docs/spike/journey5_timing.jsonl").read_text(encoding="utf-8").splitlines())
        tl = correlate(rec, log)
        scrolls = [s for s in tl.steps if s.member == "firstVisibleRow"]
        assert scrolls and all(s.strategy == "watch-run" for s in scrolls)
        assert all(s.t_start is not None for s in scrolls)
        nav = [s for s in tl.steps if s.member in ("sendVKey",) ]
        assert all(s.strategy in ("fingerprint-screen", "exact-focus") for s in nav)
```

- [ ] **Step 4: Run; fix matcher orderings until green.** Commit: `test(correlate): committed journey-5 pair acceptance; local journey-6 gate`.

---

### Task 5: JSONL + markdown renderers, sbar + transcript attachment

**Files:**
- Modify: `src/sapsucker/_correlate.py`
- Test: `unittests/test_correlate.py`

- [ ] **Step 1: Failing tests**

```python
class TestRender:
    def test_jsonl_shape(self):
        tl = ...  # small synthetic timeline from task 2 fixtures
        row = json.loads(tl.to_jsonl().splitlines()[0])
        assert row["line_no"] == 1
        assert row["strategy"] == "recorder-boilerplate"
        assert row["args"] == ["152", "33", "false"]
        assert row["t_start"] is None

    def test_markdown_has_table_and_summary(self):
        md = tl.to_markdown()
        assert "| line |" in md
        assert "exact-focus" in md
        assert tl.strategy_counts["exact-focus"] >= 1

class TestSbarAndTranscript:
    def test_sbar_text_attached(self):
        # log rows include "sbar_text"; the anchor sample and its predecessors carry a message
        ...
        assert tl.steps[k].sbar_text == "Employee created"

    def test_transcript_intersects_window(self):
        rec = ...  # one press step at t=2
        entries = [TranscriptEntry(1.5, 2.5, "jetzt speichere ich"), TranscriptEntry(8.0, 9.0, "fertig")]
        tl = correlate(rec, log, transcript=entries)
        assert tl.steps[0].transcript == ("jetzt speichere ich",)
```

- [ ] **Step 2: Verify failure; implement; verify pass.**

`to_jsonl()`: one flat JSON object per step (same field names as the dataclass, `args` as list, ISO floats for times). `to_markdown()`: summary block (strategy counts, clock origin note, recorder skew) + one table row per step (line, member, t_start/t_end, strategy, confidence, flags, transcript joined by " / ", sbar text truncated to 40 chars). Transcript input: normalized `TranscriptEntry` list; CLI-side SRT parser converts (`00:00:01,500 --> 00:00:02,500`). Intersection rule: entry overlaps `[t_start - 2, t_end or t_start + 2]`.

- [ ] **Step 3: Commit:** `feat(correlate): JSONL + markdown renderers, sbar and transcript attachment`.

---

### Task 6: CLI + entry-point shim + pyproject wiring

**Files:**
- Create: `src/sapsucker/correlate_cli.py`
- Create: `src/sapsucker/_correlate_entry.py`
- Modify: `pyproject.toml` ([project.scripts])
- Test: `unittests/test_correlate.py` (typer CliRunner against the committed pair)

- [ ] **Step 1: Failing CLI test** (CliRunner, input = `journey5_bp.vbs` + `journey5_timing.jsonl`, `--out`/`--markdown` to tmp_path; assert exit 0, files non-empty, md contains "watch-run").
- [ ] **Step 2: Implement** CLI:

```python
app = typer.Typer(add_completion=False, help="Join a recorded .vbs, a monitor JSONL and an optional transcript into one timeline.")

@app.command()
def main(
    recording: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    monitor_log: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    transcript: Annotated[Path | None, typer.Option("--transcript", "-t")] = None,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("timeline.jsonl"),
    markdown: Annotated[Path | None, typer.Option("--markdown", "-m")] = None,
) -> None:
```

SRT parser lives in `correlate_cli.py` (STT choice stays out of the library, per the plan doc). Entry shim mirrors `_monitor_entry.py` verbatim in structure. Add to `pyproject.toml`:

```toml
[project.scripts]
sapsucker-monitor = "sapsucker._monitor_entry:main"
sapsucker-correlate = "sapsucker._correlate_entry:main"
```

- [ ] **Step 3: `uv sync --group tests`, run pytest + the new CLI test.**
- [ ] **Step 4: Commit:** `feat(correlate): sapsucker-correlate CLI`.

---

### Task 7: Gates + README + PR

- [ ] **Step 1: Full gate sequence (each individually, per AGENTS.md):**

```bash
uv run pytest -q                      # unittests
uv run ruff format --check .          # formatting gate 1
uv run ruff check --select I .        # formatting gate 2
uv run ruff check src/sapsucker unittests
uv run mypy --show-error-codes src/sapsucker --strict
uv run mypy --show-error-codes examples/sapsucker --strict --ignore-missing-imports
uv run codespell_lib src README.md docs unittests   # check actual spelling gate command in CI
```

- [ ] **Step 2: README** — add "Correlating a recording with its log" subsection (after the monitor section): what it joins, the one-liner `sapsucker-correlate journey3.vbs timing.jsonl -m timeline.md`, strategy list one-liner, and the 17/18 cross-journey evidence with its caveat (prototype score on one real pair; timeline needs a human eyeball — plan doc's acceptance note).

- [ ] **Step 3: Public-repo hygiene check on the diff:** no hostnames, no client numbers, no `/XXX/` namespace, no personal names beyond the already-masked corpus. The temp JSONL path must NOT appear in any committed file (use the machine-specific path only in the skipif-gated test, which is acceptable because it reveals nothing about SAP systems — but prefer checking it reads as neutral; if in doubt, gate it on an env var `SAPSUCKER_J6_JSONL` instead of a hard-coded path).

Decision: use env var `SAPSUCKER_CORRELATE_J6` (path), so nothing machine-specific is committed:

```python
J6 = os.environ.get("SAPSUCKER_CORRELATE_J6")
@pytest.mark.skipif(not J6 or not Path(J6).exists(), ...)
```

- [ ] **Step 4: Commit:** `docs: correlate CLI in README; gates green`.
- [ ] **Step 5: Push branch, open PR against `Hochfrequenz/sapsucker` main, referencing #126 (and #95 where relevant).** Then the review loop: internal agent review → fix → Copilot review → fix → CI green → ask user re: merge (2-review rule; admin-bypass only the extra human approval after both reviews are done — never bypass CI).

---

## Post-merge follow-ups (not this PR)

- Phase 2 member-gap check (the other missing phase) — separate PR.
- Comment on #126/#121 with the acceptance evidence and close.
- Then the sapgui.mcp bug triage (#789, #798, #878…).
