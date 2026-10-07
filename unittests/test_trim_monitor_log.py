"""Tests for ``scripts/trim_monitor_log.py``, the tool that produced the journey-6 fixture."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

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
