"""Tests for the --record option of sapsucker-monitor (#125) — fakes only, no SAP."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from typer.testing import CliRunner

from sapsucker import monitor_cli
from sapsucker.monitor import SCHEMA_VERSION, Sample

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


def test_bad_record_name_is_rejected_before_attach_and_leaves_out_untouched(monkeypatch, tmp_path: Path):
    def _no_attach():
        raise AssertionError("must not attach")

    monkeypatch.setattr(monitor_cli, "_attach", _no_attach)
    out = tmp_path / "t.jsonl"
    out.write_text("keep me", encoding="utf-8")
    result = runner.invoke(monitor_cli.app, ["--out", str(out), "--record", "bad_name.vbs"])
    assert result.exit_code == 2
    assert out.read_text(encoding="utf-8") == "keep me"


def test_recorder_refusal_leaves_existing_out_untouched(monkeypatch, tmp_path: Path):
    session = _session()
    session.start_recording.side_effect = RuntimeError("refused")
    _patch(monkeypatch, session)
    out = tmp_path / "t.jsonl"
    out.write_text("keep me", encoding="utf-8")
    runner.invoke(monitor_cli.app, ["--out", str(out), "--record", "j1.vbs"])
    assert out.read_text(encoding="utf-8") == "keep me"


def test_recorder_is_stopped_when_the_sample_loop_fails(monkeypatch, tmp_path: Path):
    class _Failing(_OneSampleMonitor):
        def samples(self, origin=None):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    session = _session()
    monkeypatch.setattr(monitor_cli, "_attach", lambda: session)
    monkeypatch.setattr(monitor_cli, "SessionMonitor", _Failing)
    result = runner.invoke(monitor_cli.app, ["--out", str(tmp_path / "t.jsonl"), "--record", "j1.vbs"])
    assert result.exit_code == 1
    session.stop_recording.assert_called_once()


def test_recorder_is_stopped_when_out_cannot_be_opened(monkeypatch, tmp_path: Path):
    session = _session()
    _patch(monkeypatch, session)
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    result = runner.invoke(monitor_cli.app, ["--out", str(blocker / "t.jsonl"), "--record", "j1.vbs"])
    assert result.exit_code == 1
    session.stop_recording.assert_called_once()


def test_failure_to_stop_the_recorder_exits_nonzero(monkeypatch, tmp_path: Path):
    session = _session()
    session.stop_recording.side_effect = RuntimeError("stuck")
    _patch(monkeypatch, session)
    result = runner.invoke(monitor_cli.app, ["--out", str(tmp_path / "t.jsonl"), "--record", "j1.vbs"])
    assert result.exit_code == 1


def test_header_carries_schema_version(monkeypatch, tmp_path: Path):
    _patch(monkeypatch, _session())
    out = tmp_path / "t.jsonl"
    runner.invoke(monitor_cli.app, ["--out", str(out), "--record", "j1.vbs"])
    header = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert header["schema_version"] == SCHEMA_VERSION
