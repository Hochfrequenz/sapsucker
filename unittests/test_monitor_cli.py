"""Tests for the --record option of sapsucker-monitor (#125) — fakes only, no SAP."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from typer.testing import CliRunner

from sapsucker import monitor_cli
from sapsucker.monitor import Sample

runner = CliRunner()


class _OneSampleMonitor:
    def __init__(self, session: Any, **_: Any) -> None:
        pass

    def samples(self, origin: float | None = None):
        assert origin is not None
        yield Sample(seq=0, at="2026-01-01T00:00:00+00:00", elapsed=timedelta(seconds=1), values={"transaction": "BP"})
        raise KeyboardInterrupt


def _session() -> MagicMock:
    session = MagicMock()
    session.start_recording.return_value = r"C:\Scripts\J1.VBS"
    return session


def _patch(monkeypatch, session):
    monkeypatch.setattr(monitor_cli, "_attach", lambda: session)
    monkeypatch.setattr(monitor_cli, "SessionMonitor", _OneSampleMonitor)


def test_record_starts_recorder_writes_header_and_stops_recorder(monkeypatch, tmp_path: Path):
    session = _session()
    _patch(monkeypatch, session)
    out = tmp_path / "t.jsonl"
    result = runner.invoke(monitor_cli.app, ["--out", str(out), "--record", "j1.vbs"])
    assert result.exit_code == 0, result.output
    session.start_recording.assert_called_once_with("j1.vbs")
    session.stop_recording.assert_called_once()
    header, sample = (json.loads(line) for line in out.read_text(encoding="utf-8").splitlines())
    assert header["record_type"] == "header"
    assert header["recording_file"] == r"C:\Scripts\J1.VBS"
    assert header["recorder_skew"] >= 0
    assert sample["transaction"] == "BP"


def test_without_record_there_is_no_header_and_no_recorder(monkeypatch, tmp_path: Path):
    session = _session()
    _patch(monkeypatch, session)
    out = tmp_path / "t.jsonl"
    result = runner.invoke(monitor_cli.app, ["--out", str(out)])
    assert result.exit_code == 0, result.output
    session.start_recording.assert_not_called()
    assert [json.loads(line).get("record_type") for line in out.read_text(encoding="utf-8").splitlines()] == [None]


def test_bad_record_name_exits_2(monkeypatch, tmp_path: Path):
    session = _session()
    session.start_recording.side_effect = ValueError("invalid recording filename")
    _patch(monkeypatch, session)
    result = runner.invoke(monitor_cli.app, ["--out", str(tmp_path / "t.jsonl"), "--record", "bad_name.vbs"])
    assert result.exit_code == 2


def test_recorder_refusal_exits_1_with_rz11_hint(monkeypatch, tmp_path: Path):
    session = _session()
    session.start_recording.side_effect = RuntimeError("refused")
    _patch(monkeypatch, session)
    result = runner.invoke(monitor_cli.app, ["--out", str(tmp_path / "t.jsonl"), "--record", "j1.vbs"])
    assert result.exit_code == 1
    assert "user_scripting_disable_recording" in result.output
