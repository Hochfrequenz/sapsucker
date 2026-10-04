"""Tests for sapsucker.correlate (recording → artefact phase 5, #126)."""

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from sapsucker._correlate import (
    MonitorLog,
    TranscriptEntry,
    correlate,
    load_monitor_log,
)
from sapsucker._recording import Recording


def _log(*rows):
    """Build a MonitorLog from (elapsed, changed, values) rows (v2 flat schema)."""
    return load_monitor_log(
        [
            json.dumps({"seq": i, "elapsed_s": e, "changed": list(c), **v})
            for i, (e, c, v) in enumerate(rows)
        ]
    )


class TestLoadMonitorLog:
    def test_v2_flat_schema(self):
        lines = [
            json.dumps(
                {
                    "seq": 0,
                    "elapsed_s": 0.015,
                    "changed": [],
                    "transaction": "SE16N",
                    "focus_id": "<unreadable>",
                    "wnd[0]:Text": "T",
                }
            ),
            json.dumps(
                {
                    "seq": 1,
                    "elapsed_s": 0.218,
                    "changed": ["focus_id"],
                    "transaction": "SE16N",
                    "focus_id": "/app/con[0]/ses[0]/wnd[0]/usr/txtF",
                    "wnd[0]:Text": "T",
                }
            ),
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
            json.dumps(
                {
                    "schema_version": 3,
                    "record_type": "header",
                    "origin_at": "2026-08-26T14:17:28+02:00",
                    "recording_file": "JOURNEY6.VBS",
                    "recorder_skew": 0.42,
                }
            ),
            json.dumps(
                {
                    "schema_version": 3,
                    "seq": 0,
                    "at": "2026-08-26T14:17:28.354+02:00",
                    "elapsed": "PT0.015S",
                    "changed": [],
                    "transaction": "SE16N",
                    "focus_id": "x",
                }
            ),
        ]
        log = load_monitor_log(lines)
        assert log.recorder_skew == pytest.approx(0.42)
        assert log.clock_origin_assumed is False
        assert log.samples[0].elapsed == pytest.approx(0.015)

    def test_iso_duration_long_forms(self):
        line = json.dumps(
            {
                "schema_version": 3,
                "seq": 0,
                "elapsed": "PT1H2M3.5S",
                "changed": [],
                "focus_id": "x",
            }
        )
        log = load_monitor_log([line])
        assert log.samples[0].elapsed == pytest.approx(3600 + 120 + 3.5)

    def test_non_json_line_raises(self):
        first = json.dumps({"seq": 0, "elapsed_s": 0.0, "changed": [], "focus_id": "x"})
        with pytest.raises(ValueError, match="line 2"):
            load_monitor_log([first, "not json"])

    def test_sample_without_elapsed_raises(self):
        with pytest.raises(ValueError, match="line 1"):
            load_monitor_log(["{}"])

    def test_empty_log_raises(self):
        with pytest.raises(ValueError, match="no samples"):
            load_monitor_log([])

    def test_watched_keys_become_values(self):
        line = json.dumps(
            {
                "seq": 0,
                "elapsed_s": 0.0,
                "changed": [],
                "wnd[0]/shellcont/shell:FirstVisibleRow": "0",
            }
        )
        log = load_monitor_log([line])
        assert log.samples[0].values["wnd[0]/shellcont/shell:FirstVisibleRow"] == "0"
