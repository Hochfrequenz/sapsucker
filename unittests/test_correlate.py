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

# The real journey-6 JSONL (4134 samples) lives only on the machine that
# recovered it from git history (commit 3581a44^:journey6_bp_timing.jsonl);
# point SAPSUCKER_CORRELATE_J6 at it to run the cross-journey acceptance gate.
J6 = os.environ.get("SAPSUCKER_CORRELATE_J6")


def _log(*rows):
    """Build a MonitorLog from (elapsed, changed, values) rows (v2 flat schema)."""
    return load_monitor_log(
        [json.dumps({"seq": i, "elapsed_s": e, "changed": list(c), **v}) for i, (e, c, v) in enumerate(rows)]
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
            'session.findById("wnd[0]/usr/txtA").text = "1"\nsession.findById("wnd[0]/usr/txtA").text = "2"\n'
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
            'session.findById("wnd[0]/usr/txtA").text = "1"\nsession.findById("wnd[0]/usr/txtB").text = "2"\n'
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


class TestModalBracket:
    def test_two_presses_share_one_modal_window(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/tbar[0]/btn[11]").press\n'
            'session.findById("wnd[1]/usr/btnBUTTON_1").press\n'
            'session.findById("wnd[1]/usr/btnBUTTON_2").press\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/btn[11]", "wnd[1]:Text": "<absent>"}),
            (2.0, ["wnd[1]:Text"], {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/btn[11]", "wnd[1]:Text": "Warnung"}),
            (4.0, ["wnd[1]:Text"], {"focus_id": f"{FOCUS}/wnd[0]/usr", "wnd[1]:Text": "<absent>"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[1].strategy == "modal-bracket"
        assert tl.steps[1].t_start == pytest.approx(2.0)
        assert tl.steps[1].t_end == pytest.approx(4.0)
        # sibling press shares the same consumed bracket
        assert tl.steps[2].strategy == "modal-bracket"
        assert tl.steps[2].t_start == pytest.approx(2.0)

    def test_two_sequential_modals_get_separate_brackets(self):
        # Two wnd[1] dialogs at different times: each press binds to its own
        # bracket, not the first one forever.
        rec = Recording.parse('session.findById("wnd[1]/usr/btnA").press\nsession.findById("wnd[1]/usr/btnB").press\n')
        log = _log(
            (1.0, ["focus_id"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
            (2.0, ["focus_id", "wnd[1]:Text"], {"wnd[1]:Text": "First", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (3.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (5.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Second", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (6.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].t_start == pytest.approx(2.0)
        assert tl.steps[0].t_end == pytest.approx(3.0)
        assert tl.steps[1].t_start == pytest.approx(5.0)
        assert tl.steps[1].t_end == pytest.approx(6.0)


class TestFingerprints:
    def test_okcd_pair_binds_to_transaction_change(self):
        # The okcd assignment produced no focus change (the field already had
        # focus — the keyboard-anchor case observed live), so it must bind to
        # the transition its sendVKey causes, and the sendVKey inherits it.
        rec = Recording.parse(
            'session.findById("wnd[0]/tbar[0]/okcd").text = "/nbp"\nsession.findById("wnd[0]").sendVKey 0\n'
        )
        log = _log(
            (
                1.0,
                [],
                {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/okcd", "transaction": "SESSION_MANAGER", "screen_number": 100},
            ),
            (
                2.5,
                ["transaction", "program", "screen_number"],
                {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/okcd", "transaction": "BP", "screen_number": 3000},
            ),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "fingerprint-screen"
        assert tl.steps[0].flags == ("keyboard-anchor",)
        assert tl.steps[0].t_start == pytest.approx(2.5)
        # the sendVKey inherits its okcd pair's anchor
        assert tl.steps[1].strategy == "fingerprint-screen"
        assert tl.steps[1].t_start == pytest.approx(2.5)
        assert tl.steps[1].flags == ("keyboard-anchor", "sub-interval-collapse")

    def test_press_binds_to_title_change(self):
        # No focus change at all (focus was already on the button): only the
        # title fingerprint can timestamp this press.
        rec = Recording.parse('session.findById("wnd[0]/tbar[0]/btn[11]").press\n')
        log = _log(
            (
                1.0,
                [],
                {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/btn[11]", "wnd[0]:Text": "Person anlegen", "screen_number": 3000},
            ),
            (
                2.0,
                ["wnd[0]:Text"],
                {
                    "focus_id": f"{FOCUS}/wnd[0]/tbar[0]/btn[11]",
                    "wnd[0]:Text": "Person anzeigen: 3961",
                    "screen_number": 3000,
                },
            ),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "fingerprint-title"
        assert tl.steps[0].t_start == pytest.approx(2.0)


class TestWatchRun:
    KEY = "wnd[0]/shellcont/shell:FirstVisibleRow"

    def test_first_visible_row_assignments_use_watch_key(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 1\n'
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8\n'
        )
        key = self.KEY
        log = _log(
            (1.0, [], {key: "0"}),
            (2.0, [key], {key: "1"}),
            (5.0, [key], {key: "8"}),
            (6.0, [key], {key: "55"}),  # monitor caught an extra scroll
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "watch-run"
        assert tl.steps[0].t_start == pytest.approx(2.0)
        assert tl.steps[1].strategy == "watch-run"
        assert tl.steps[1].t_start == pytest.approx(5.0)

    def test_value_mismatch_flagged_not_failed(self):
        rec = Recording.parse('session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 470\n')
        key = self.KEY
        log = _log(
            (1.0, [], {key: "0"}),
            (2.0, [key], {key: "144"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "watch-run"
        assert tl.steps[0].flags == ("value-mismatch",)
        assert tl.steps[0].t_start == pytest.approx(2.0)

    def test_exact_focus_wins_over_watch_run(self):
        # In a real recording the focus moves to the shell before the scroll
        # assignment, so exact-focus legitimately timestamps the first step;
        # the watch-run key is not even in this log.
        rec = Recording.parse('session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8\n')
        log = _log((1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell"}))
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "exact-focus"


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


@pytest.mark.skipif(
    not J6 or not Path(J6).exists(),
    reason="journey-6 JSONL is not committed (scratch corpus); set SAPSUCKER_CORRELATE_J6 to run locally",
)
class TestJourney6Acceptance:
    """The cross-journey pairing the prototype scored 17/18 on (#126).

    journey3_bp.vbs (committed) against the real journey-6 monitor log: the
    same task family, recorded on different runs — the layout variance the
    ddic-suffix strategy exists for.
    """

    def test_cross_journey_score(self):
        rec = Recording.load("docs/spike/journey3_bp.vbs")
        log = load_monitor_log(Path(J6).read_text(encoding="utf-8").splitlines())
        tl = correlate(rec, log)
        counts = Counter(s.strategy for s in tl.steps)
        assert counts["exact-focus"] == 10
        assert counts["ddic-suffix"] == 3
        assert counts["modal-bracket"] == 2
        assert counts["fingerprint-screen"] == 1
        assert counts["recorder-boilerplate"] == 1
        assert counts["unmatched"] == 0, counts


class TestSbarAndTranscript:
    def test_sbar_text_attached_at_anchor(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/ctxtGD-TAB").text = "T000"\n')
        log = _log(
            (0.5, ["sbar_text"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtGD-TAB", "sbar_text": "4 Einträge gefunden"}),
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtGD-TAB", "sbar_text": "4 Einträge gefunden"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].sbar_text == "4 Einträge gefunden"

    def test_sbar_none_without_sbar_keys(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/ctxtGD-TAB").text = "T000"\n')
        log = _log((1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtGD-TAB"}))
        tl = correlate(rec, log)
        assert tl.steps[0].sbar_text is None

    def test_transcript_intersects_window(self):
        rec = Recording.parse('session.findById("wnd[0]/tbar[0]/btn[11]").press\n')
        log = _log(
            (1.0, [], {"wnd[0]:Text": "A", "screen_number": 3000}),
            (2.0, ["wnd[0]:Text"], {"wnd[0]:Text": "B", "screen_number": 3000}),
        )
        entries = (
            TranscriptEntry(1.5, 2.5, "jetzt speichere ich"),
            TranscriptEntry(8.0, 9.0, "fertig"),
        )
        tl = correlate(rec, log, transcript=entries)
        assert tl.steps[0].transcript == ("jetzt speichere ich",)

    def test_transcript_outside_window_not_attached(self):
        rec = Recording.parse('session.findById("wnd[0]/tbar[0]/btn[11]").press\n')
        log = _log(
            (1.0, [], {"wnd[0]:Text": "A", "screen_number": 3000}),
            (2.0, ["wnd[0]:Text"], {"wnd[0]:Text": "B", "screen_number": 3000}),
        )
        entries = (TranscriptEntry(8.0, 9.0, "fertig"),)
        tl = correlate(rec, log, transcript=entries)
        assert tl.steps[0].transcript == ()


class TestRender:
    def _tl(self):
        rec = Recording.parse(self_REC)
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell"}),
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/okcd"}),
        )
        return correlate(rec, log)

    def test_jsonl_shape(self):
        tl = self._tl()
        rows = [json.loads(line) for line in tl.to_jsonl().splitlines()]
        assert len(rows) == len(tl.steps)
        first = rows[0]
        assert first["line_no"] == 1
        assert first["strategy"] == "recorder-boilerplate"
        assert first["args"] == ["152", "33", "false"]
        assert first["t_start"] is None

    def test_markdown_has_table_and_summary(self):
        md = self._tl().to_markdown()
        assert "exact-focus" in md
        assert "| line |" in md


self_REC = (
    'session.findById("wnd[0]").resizeWorkingPane 152,33,false\n'
    'session.findById("wnd[0]/tbar[0]/okcd").text = "/nse16n"\n'
)


class TestCli:
    def test_cli_on_committed_pair(self, tmp_path):
        """The committed journey-5 pair runs through the real CLI."""
        from typer.testing import CliRunner

        from sapsucker.correlate_cli import app

        out = tmp_path / "timeline.jsonl"
        md = tmp_path / "timeline.md"
        result = CliRunner().invoke(
            app,
            [
                "docs/spike/journey5_bp.vbs",
                "docs/spike/journey5_timing.jsonl",
                "--out",
                str(out),
                "--markdown",
                str(md),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists() and out.read_text(encoding="utf-8").strip()
        assert "watch-run" in md.read_text(encoding="utf-8")

    def test_cli_missing_transcript_format_exits_2(self, tmp_path):
        from typer.testing import CliRunner

        from sapsucker.correlate_cli import app

        bad = tmp_path / "bad.srt"
        bad.write_text("this is not an srt file\n", encoding="utf-8")
        result = CliRunner().invoke(
            app,
            [
                "docs/spike/journey5_bp.vbs",
                "docs/spike/journey5_timing.jsonl",
                "--transcript",
                str(bad),
                "--out",
                str(tmp_path / "t.jsonl"),
            ],
        )
        assert result.exit_code == 2

    def test_parse_srt_round_trip(self):
        from sapsucker.correlate_cli import parse_srt

        srt = "1\n00:00:01,500 --> 00:00:02,500\njetzt speichere ich\n\n2\n00:00:08,000 --> 00:00:09,000\nfertig\n"
        entries = parse_srt(srt)
        assert len(entries) == 2
        assert entries[0].t_start == pytest.approx(1.5)
        assert entries[0].text == "jetzt speichere ich"
