"""Tests for ``scripts/trim_monitor_log.py``, the tool that produced the journey-6 fixture."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from sapsucker._correlate import correlate, load_monitor_log
from sapsucker._recording import Recording
from sapsucker.monitor import SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent
J6_TRIMMED = REPO_ROOT / "docs" / "spike" / "journey6_bp_timing.trimmed.jsonl"


def _load_trim() -> ModuleType:
    """Import the script, which is not a package and not on sys.path."""
    spec = importlib.util.spec_from_file_location("trim_monitor_log", REPO_ROOT / "scripts" / "trim_monitor_log.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _line(seq: int, changed: list[str]) -> str:
    return json.dumps({"seq": seq, "elapsed_s": seq * 0.2, "changed": changed})


def test_committed_fixture_is_a_fixed_point_of_the_trim_rule():
    # Ties the fixture to the documented rule: re-trimming it drops nothing,
    # so every line in it is one the rule keeps.
    lines = J6_TRIMMED.read_text(encoding="utf-8").splitlines()
    assert _load_trim().trim(lines, None) == lines


def test_keeps_first_sample_changes_and_their_predecessors():
    lines = [_line(0, []), _line(1, []), _line(2, []), _line(3, ["focus_id"]), _line(4, []), _line(5, [])]
    assert _load_trim().trim(lines, None) == [lines[0], lines[2], lines[3]]


def test_empty_input_gives_empty_output():
    assert _load_trim().trim([], None) == []
    assert _load_trim().trim(["", "  "], None) == []


def _header() -> str:
    # Built the way ``sapsucker-monitor --record`` writes it (monitor_cli.py):
    # a v3 header record with no ``seq``, before the first sample.
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "record_type": "header",
            "origin_at": "2026-10-07T12:00:00+02:00",
            "recording_file": "sbarwindow.vbs",
            "recorder_skew": 1.5,
        }
    )


def test_header_kept_and_baseline_is_first_sample_without_cutoff():
    samples = [_line(0, []), _line(1, []), _line(2, ["focus_id"]), _line(3, [])]
    out = _load_trim().trim([_header(), *samples], None)
    assert out == [_header(), samples[0], samples[1], samples[2]]
    log = load_monitor_log(out)
    assert log.recorder_skew == 1.5
    assert [s.seq for s in log.samples] == [0, 1, 2]


def test_header_kept_with_cutoff():
    samples = [_line(0, []), _line(1, ["focus_id"]), _line(2, []), _line(3, ["focus_id"])]
    out = _load_trim().trim([_header(), *samples], 2)
    assert out == [_header(), samples[0], samples[1]]
    assert load_monitor_log(out).recorder_skew == 1.5


def _modal_line(seq: int, changed: list[str], title: str) -> str:
    return json.dumps({"seq": seq, "elapsed_s": float(seq), "changed": changed, "wnd[1]:Text": title})


def test_final_sample_kept_while_a_modal_is_still_open():
    # _modal_brackets ends a dialog still open at log end on the final sample;
    # dropping the idle tail would shrink its bracket from 1s-3s to 1s-1s.
    lines = [
        _modal_line(0, [], "<absent>"),
        _modal_line(1, ["wnd[1]:Text"], "Warnung"),
        _modal_line(2, [], "Warnung"),
        _modal_line(3, [], "Warnung"),
    ]
    out = _load_trim().trim(lines, None)
    assert out == [lines[0], lines[1], lines[3]]
    rec = Recording.parse(
        'session.findById("wnd[1]/usr/btnBUTTON_1").press\nsession.findById("wnd[1]/usr/btnBUTTON_2").press\n'
    )
    full = correlate(rec, load_monitor_log(lines))
    trimmed = correlate(rec, load_monitor_log(out))
    assert [(s.strategy, s.t_start, s.t_end) for s in full.steps] == [("modal-bracket", 1.0, 3.0)] * 2
    assert trimmed.steps == full.steps
    # With a cutoff the final *in-range* sample is the one kept.
    assert _load_trim().trim([*lines, _modal_line(4, [], "Warnung")], 3) == out


def test_idle_tail_still_dropped_once_the_modal_closed():
    lines = [
        _modal_line(0, [], "<absent>"),
        _modal_line(1, ["wnd[1]:Text"], "Warnung"),
        _modal_line(2, ["wnd[1]:Text"], "<absent>"),
        _modal_line(3, [], "<absent>"),
    ]
    assert _load_trim().trim(lines, None) == lines[:3]
