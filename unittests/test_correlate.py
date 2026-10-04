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

SPIKE = Path(__file__).parent.parent / "docs" / "spike"

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
        # The wnd[0] step in between moves the cursor past the first modal's close.
        rec = Recording.parse(
            'session.findById("wnd[1]/usr/btnA").press\n'
            'session.findById("wnd[0]/usr/txtZ").text = "x"\n'
            'session.findById("wnd[1]/usr/btnB").press\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
            (2.0, ["focus_id", "wnd[1]:Text"], {"wnd[1]:Text": "First", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (3.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (4.0, ["focus_id"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr/txtZ"}),
            (5.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Second", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (6.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].t_start == pytest.approx(2.0)
        assert tl.steps[0].t_end == pytest.approx(3.0)
        assert tl.steps[1].t_start == pytest.approx(4.0)
        assert tl.steps[2].t_start == pytest.approx(5.0)
        assert tl.steps[2].t_end == pytest.approx(6.0)

    def test_siblings_share_first_modal_even_when_same_window_opens_again_later(self):
        rec = Recording.parse('session.findById("wnd[1]/usr/btnA").press\nsession.findById("wnd[1]/usr/btnB").press\n')
        log = _log(
            (1.0, ["wnd[1]:Text"], {"wnd[1]:Text": "First", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (2.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
            (5.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Second", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (6.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
        )
        tl = correlate(rec, log)
        assert [(x.t_start, x.t_end) for x in tl.steps] == [(1.0, 2.0), (1.0, 2.0)]

    def test_focus_returning_on_the_close_sample_does_not_reuse_the_closed_modal(self):
        # The close sample is the first one without the modal, and focus usually
        # returns to wnd[0] on that very sample: the wnd[0] step then sits *at*
        # close_idx, and the next wnd[1] step must take the second modal.
        rec = Recording.parse(
            'session.findById("wnd[1]/usr/btnA").press\n'
            'session.findById("wnd[0]/usr/txtZ").text = "x"\n'
            'session.findById("wnd[1]/usr/btnB").press\n'
        )
        log = _log(
            (1.0, ["wnd[1]:Text"], {"wnd[1]:Text": "First", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (2.0, ["wnd[1]:Text", "focus_id"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr/txtZ"}),
            (5.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Second", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (6.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
        )
        tl = correlate(rec, log)
        assert (tl.steps[0].t_start, tl.steps[0].t_end) == (1.0, 2.0)
        assert (tl.steps[2].t_start, tl.steps[2].t_end) == (5.0, 6.0)

    def test_modal_closed_at_the_cursor_is_skipped_when_no_sibling_cached_it(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtZ").text = "x"\nsession.findById("wnd[1]/usr/btnB").press\n'
        )
        log = _log(
            (1.0, ["wnd[1]:Text"], {"wnd[1]:Text": "First", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (2.0, ["wnd[1]:Text", "focus_id"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr/txtZ"}),
            (5.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Second", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (6.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
        )
        tl = correlate(rec, log)
        assert (tl.steps[1].t_start, tl.steps[1].t_end) == (5.0, 6.0)

    def test_siblings_share_modal_after_cursor_moved_inside_it(self):
        rec = Recording.parse(
            'session.findById("wnd[1]/usr/txtX").text = "1"\n'
            'session.findById("wnd[1]/usr/btnA").press\n'
            'session.findById("wnd[1]/usr/btnB").press\n'
        )
        log = _log(
            (1.0, ["wnd[1]:Text"], {"wnd[1]:Text": "First", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
            (2.0, ["focus_id"], {"wnd[1]:Text": "First", "focus_id": f"{FOCUS}/wnd[1]/usr/txtX"}),
            (3.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
            (5.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Second", "focus_id": f"{FOCUS}/wnd[1]/usr"}),
            (6.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
        )
        tl = correlate(rec, log)
        assert [(x.t_start, x.t_end) for x in tl.steps[1:]] == [(2.0, 3.0), (2.0, 3.0)]

    def test_press_after_typing_into_the_same_modal_still_matches_it(self):
        # Typing into the dialog moves the cursor past the bracket's open sample;
        # the confirming press must still bind to that dialog, not go unmatched.
        rec = Recording.parse(
            'session.findById("wnd[1]/usr/txtX").text = "1"\nsession.findById("wnd[1]/tbar[0]/btn[0]").press\n'
        )
        log = _log(
            (1.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Dialog", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
            (2.0, ["focus_id"], {"wnd[1]:Text": "Dialog", "focus_id": f"{FOCUS}/wnd[1]/usr/txtX"}),
            (3.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>", "focus_id": f"{FOCUS}/wnd[0]/usr"}),
        )
        tl = correlate(rec, log)
        assert tl.steps[0].strategy == "exact-focus"
        assert tl.steps[1].strategy == "modal-bracket"
        assert tl.steps[1].t_start == pytest.approx(2.0)
        assert tl.steps[1].t_end == pytest.approx(3.0)


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


class TestDurationsAndSkew:
    @pytest.mark.parametrize(
        ("iso", "seconds"),
        [("PT1M", 60.0), ("PT1H", 3600.0), ("PT1H1M", 3660.0), ("P1D", 86400.0), ("P1DT1H", 90000.0), ("PT0.5S", 0.5)],
    )
    def test_iso_durations_pydantic_emits(self, iso, seconds):
        line = json.dumps({"seq": 0, "elapsed": iso, "changed": []})
        assert load_monitor_log([line]).samples[0].elapsed == pytest.approx(seconds)

    @pytest.mark.parametrize("iso", ["P", "PT", "1M", "PTM"])
    def test_iso_durations_without_a_component_are_rejected(self, iso):
        with pytest.raises(ValueError, match="elapsed"):
            load_monitor_log([json.dumps({"seq": 0, "elapsed": iso, "changed": []})])

    def test_recorder_skew_shifts_transcript_cues(self):
        header = json.dumps({"record_type": "header", "recorder_skew": 3.0})
        rows = [
            json.dumps({"seq": 0, "elapsed_s": 10.0, "changed": [], "wnd[0]:Text": "A", "screen_number": 1}),
            json.dumps(
                {"seq": 1, "elapsed_s": 11.0, "changed": ["wnd[0]:Text"], "wnd[0]:Text": "B", "screen_number": 1}
            ),
        ]
        log = load_monitor_log([header, *rows])
        assert log.recorder_skew == 3.0
        rec = Recording.parse('session.findById("wnd[0]/tbar[0]/btn[11]").press\n')
        # The press sits at 11.0 (sampler clock). A cue at 8.5s of the *recording*
        # is 11.5s on the sampler clock and falls inside; unshifted it would be
        # outside the +-2s window around 11.0.
        cue = (TranscriptEntry(8.5, 8.6, "shifted"),)
        assert correlate(rec, log, transcript=cue).steps[0].transcript == ("shifted",)
        assert correlate(rec, load_monitor_log(rows), transcript=cue).steps[0].transcript == ()
        # Upper bound: 10.5s of the recording is 13.5s on the sampler clock, past the
        # window (hi = 11.0 + 2.0); unshifted it would wrongly fall inside.
        late = (TranscriptEntry(10.5, 10.6, "late"),)
        assert correlate(rec, log, transcript=late).steps[0].transcript == ()


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
        CliRunner = pytest.importorskip("typer.testing").CliRunner

        from sapsucker.correlate_cli import app

        out = tmp_path / "timeline.jsonl"
        md = tmp_path / "timeline.md"
        result = CliRunner().invoke(
            app,
            [
                str(SPIKE / "journey5_bp.vbs"),
                str(SPIKE / "journey5_timing.jsonl"),
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
        CliRunner = pytest.importorskip("typer.testing").CliRunner

        from sapsucker.correlate_cli import app

        bad = tmp_path / "bad.srt"
        bad.write_text("this is not an srt file\n", encoding="utf-8")
        result = CliRunner().invoke(
            app,
            [
                str(SPIKE / "journey5_bp.vbs"),
                str(SPIKE / "journey5_timing.jsonl"),
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

    def test_cli_bad_monitor_log_exits_2(self, tmp_path):
        CliRunner = pytest.importorskip("typer.testing").CliRunner
        from sapsucker.correlate_cli import app

        bad = tmp_path / "bad.jsonl"
        bad.write_text("not json\n", encoding="utf-8")
        result = CliRunner().invoke(app, [str(SPIKE / "journey5_bp.vbs"), str(bad), "--out", str(tmp_path / "t.jsonl")])
        assert result.exit_code == 2
        assert "bad monitor log" in result.output

    def test_cli_bad_recording_exits_2(self, tmp_path):
        CliRunner = pytest.importorskip("typer.testing").CliRunner
        from sapsucker.correlate_cli import app

        bad = tmp_path / "bad.vbs"
        bad.write_text("this is not a recording\n", encoding="utf-8")
        result = CliRunner().invoke(
            app, [str(bad), str(SPIKE / "journey5_timing.jsonl"), "--out", str(tmp_path / "t.jsonl")]
        )
        assert result.exit_code == 2, result.output
        assert "bad recording" in result.output


class TestCopilotRound:
    @pytest.mark.parametrize(
        "line",
        [
            '{"seq": 0, "elapsed_s": null}',
            '{"seq": null, "elapsed_s": 1.0}',
            '{"seq": 0, "elapsed_s": 1.0, "changed": 42}',
            '{"seq": 0, "elapsed_s": 1.0, "changed": "focus_id"}',
            '{"seq": 0, "elapsed_s": 1.0, "changed": [1]}',
            '{"seq": 1e999, "elapsed_s": 1.0}',
            '{"record_type": "header", "recorder_skew": []}',
        ],
    )
    def test_wrong_field_types_raise_line_numbered_value_error(self, line):
        with pytest.raises(ValueError, match="line 1"):
            load_monitor_log([line])

    def test_jsonl_carries_alignment_metadata(self):
        rec = Recording.parse('session.findById("wnd[0]/usr/txtA").text = "1"\n')
        row = {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}
        assumed = correlate(rec, _log((1.0, ["focus_id"], row)))
        record = json.loads(assumed.to_jsonl().splitlines()[0])
        assert record["clock_origin_assumed"] is True
        assert record["recorder_skew"] is None
        measured = correlate(
            rec,
            load_monitor_log(
                [
                    json.dumps({"record_type": "header", "recorder_skew": 1.5}),
                    json.dumps({"seq": 0, "elapsed_s": 1.0, "changed": ["focus_id"], **row}),
                ]
            ),
        )
        record = json.loads(measured.to_jsonl().splitlines()[0])
        assert record["clock_origin_assumed"] is False
        assert record["recorder_skew"] == 1.5

    def test_markdown_cells_are_escaped(self):
        rec = Recording.parse('session.findById("wnd[0]/tbar[0]/btn[11]").press\n')
        log = _log(
            (1.0, [], {"wnd[0]:Text": "A", "screen_number": 3000}),
            (2.0, ["wnd[0]:Text"], {"wnd[0]:Text": "B", "screen_number": 3000}),
        )
        tl = correlate(rec, log, transcript=(TranscriptEntry(1.5, 2.5, "A | B\nC"),))
        row = next(line for line in tl.to_markdown().splitlines() if line.startswith("| 1 |"))
        assert row.endswith("| A \\| B C |")
        assert row.replace("\\|", "").count("|") == 10  # 9 cells

    def test_snake_case_and_camel_case_members_share_one_watch_key(self):
        from sapsucker._correlate import _watch_key_of

        shell = "wnd[0]/shellcont/shell"
        assert _watch_key_of(shell, "first_visible_row") == f"{shell}:FirstVisibleRow"
        assert _watch_key_of(shell, "firstVisibleRow") == f"{shell}:FirstVisibleRow"

    def test_repeated_edit_with_one_observed_event_is_flagged_collapsed(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\nsession.findById("wnd[0]/usr/txtA").text = "2"\n'
        )
        log = _log((1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}))
        tl = correlate(rec, log)
        assert "sub-interval-collapse" not in tl.steps[0].flags
        assert "sub-interval-collapse" in tl.steps[1].flags

    def test_repeated_suffix_edit_takes_the_later_focus_change(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "1"\n'
            'session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "2"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtSZA13_0100-TEL_NUMBER"}),
            (9.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtSZA13_0100-TEL_NUMBER"}),
        )
        tl = correlate(rec, log)
        assert [x.strategy for x in tl.steps] == ["ddic-suffix", "ddic-suffix"]
        assert [x.t_start for x in tl.steps] == [1.0, 9.0]

    def test_repeated_suffix_edit_with_one_event_is_flagged_collapsed(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "1"\n'
            'session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "2"\n'
        )
        log = _log((1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtSZA13_0100-TEL_NUMBER"}))
        tl = correlate(rec, log)
        assert "sub-interval-collapse" not in tl.steps[0].flags
        assert "sub-interval-collapse" in tl.steps[1].flags

    def test_cli_unreadable_transcript_exits_2(self, tmp_path):
        CliRunner = pytest.importorskip("typer.testing").CliRunner
        from sapsucker.correlate_cli import app

        result = CliRunner().invoke(
            app,
            [
                str(SPIKE / "journey5_bp.vbs"),
                str(SPIKE / "journey5_timing.jsonl"),
                "--transcript",
                str(tmp_path),  # a directory: read_text raises OSError
                "--out",
                str(tmp_path / "t.jsonl"),
            ],
        )
        assert result.exit_code == 2, result.output
        assert "bad --transcript" in result.output


class TestOpusRound:
    def test_elapsed_error_is_not_double_prefixed(self):
        with pytest.raises(ValueError, match="line 1") as info:
            load_monitor_log(['{"seq": 0}'])
        assert str(info.value).count("line 1:") == 1

    def test_repeat_does_not_jump_over_another_fields_focus_change(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/usr/txtB").text = "x"\n'
            'session.findById("wnd[0]/usr/txtA").text = "3"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 2.0, 3.0]
        assert "sub-interval-collapse" in tl.steps[1].flags

    def test_suffix_repeat_does_not_jump_over_another_fields_focus_change(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "1"\n'
            'session.findById("wnd[0]/usr/txtSZA11_0100-TEL_NUMBER").text = "2"\n'
            'session.findById("wnd[0]/usr/txtSZA11_0100-FAX_NUMBER").text = "3"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtSZA13_0100-TEL_NUMBER"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtSZA13_0100-FAX_NUMBER"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtSZA14_0100-TEL_NUMBER"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 2.0]
        assert "sub-interval-collapse" in tl.steps[1].flags

    def test_boilerplate_between_edits_does_not_hide_the_repeat(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]").resizeWorkingPane 1,1,false\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps if x.strategy == "exact-focus"] == [1.0, 5.0]

    def test_repeat_leaves_an_event_for_each_later_edit_of_the_same_field(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/usr/txtA").text = "3"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 5.0]

    def test_markdown_escapes_backslashes(self):
        rec = Recording.parse('session.findById("wnd[0]/tbar[0]/btn[11]").press\n')
        log = _log(
            (1.0, [], {"wnd[0]:Text": "A", "screen_number": 3000}),
            (2.0, ["wnd[0]:Text"], {"wnd[0]:Text": "B", "screen_number": 3000}),
        )
        tl = correlate(rec, log, transcript=(TranscriptEntry(1.5, 2.5, "C:" + chr(92) + "x | B"),))
        row = next(line for line in tl.to_markdown().splitlines() if line.startswith("| 1 |"))
        bs = chr(92)
        assert row.endswith("| C:" + bs * 2 + "x " + bs + "| B |")

    def test_watch_key_keeps_inner_capitals(self):
        from sapsucker._correlate import _watch_key_of

        assert _watch_key_of("x", "current_cellColumn") == "x:CurrentCellColumn"
