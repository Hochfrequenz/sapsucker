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


FOCUS = "/app/con[0]/ses[0]"


class TestMatcherBasics:
    REC = (
        'session.findById("wnd[0]").resizeWorkingPane 152,33,false\n'
        'session.findById("wnd[0]/tbar[0]/okcd").text = "/nse16n"\n'
        'session.findById("wnd[0]/usr/ctxtGD-TAB").text = "T000"\n'
        'session.findById("wnd[0]/usr/ctxtGD-TAB").setFocus\n'
        'session.findById("wnd[0]/usr/ctxtGD-TAB").caretPosition = 4\n'
    )

    def test_exact_focus_and_boilerplate_and_collapse(self):
        log = _log(
            (0.0, [], {"transaction": "SESSION_MANAGER", "focus_id": f"{FOCUS}/wnd[0]/shellcont/shell"}),
            (1.0, ["focus_id"], {"transaction": "SESSION_MANAGER", "focus_id": f"{FOCUS}/wnd[0]/tbar[0]/okcd"}),
            (2.0, ["focus_id"], {"transaction": "SE16N", "focus_id": f"{FOCUS}/wnd[0]/usr/ctxtGD-TAB"}),
        )
        tl = correlate(Recording.parse(self.REC), log)
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

    def test_steps_never_match_backwards(self):
        # Two steps on the same field: the second must bind to the *later*
        # sample, not re-match the first step's anchor.
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (9.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].t_start == pytest.approx(1.0)
        assert tl.steps[1].t_start == pytest.approx(9.0)

    def test_ddic_suffix_flags_layout_sensitive(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "x"\n')
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtOTHER"}),
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtSZA7_D0400-TEL_NUMBER"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "ddic-suffix"
        assert tl.steps[0].flags == ("layout-sensitive",)
        assert tl.steps[0].confidence == "medium"
        assert tl.steps[0].t_start == pytest.approx(1.0)

    def test_unmatched_step(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtNOPE").text = "x"\n')
        log = _log((0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtOTHER"}))
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "unmatched"
        assert tl.steps[0].confidence == "low"
        assert tl.steps[0].t_start is None

    def test_step_cannot_bind_before_previous_anchor(self):
        # The log's txtB change happens *before* txtA's; step 2 (txtB) must not
        # reach back before step 1's anchor — the timeline stays monotonic.
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtB").text = "2"\n'
        )
        log = _log(
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (9.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].t_start == pytest.approx(9.0)
        assert tl.steps[1].strategy == "unmatched"

    def test_baseline_sample_is_never_a_focus_match(self):
        # changed must contain focus_id: a matching focus value in an unchanged sample is stale.
        rec = Recording.parse('session.findById("wnd[0]/usr/txtA").text = "1"\n')
        log = _log((0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}))
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "unmatched"

    def test_strategy_counts(self):
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell"}),
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/okcd"}),
        )
        tl = correlate(Recording.parse(self.REC), log)
        assert tl.strategy_counts["recorder-boilerplate"] == 1
        assert tl.strategy_counts["unmatched"] >= 1


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
