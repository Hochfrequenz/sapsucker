"""Tests for sapsucker.correlate (recording → artefact phase 5, #126)."""

import json
import os
import re
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

# Journey 6 (recorded 2026-08-26): the committed fixture is the full 4134-sample log
# (commit 3581a44^:journey6_bp_timing.jsonl) trimmed by scripts/trim_monitor_log.py;
# see docs/spike/README.md. SAPSUCKER_CORRELATE_J6 optionally points at the full log
# so the same assertions can be re-checked against it locally.
J6_TRIMMED = SPIKE / "journey6_bp_timing.trimmed.jsonl"
J6_FULL = os.environ.get("SAPSUCKER_CORRELATE_J6")
J6_PATHS = [pytest.param(J6_TRIMMED, id="trimmed")]
if J6_FULL and Path(J6_FULL).exists():
    J6_PATHS.append(pytest.param(Path(J6_FULL), id="full"))


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

    @pytest.mark.parametrize(("assigned", "expect_second"), [(("8", "55"), True), (("55", "8"), False)])
    def test_collapsed_watch_assignment_recomputes_value_mismatch(self, assigned, expect_second):
        rec = Recording.parse(
            "".join(f'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = {v}\n' for v in assigned)
        )
        key = self.KEY
        log = _log((1.0, [], {key: "0"}), (2.0, [key], {key: "8"}))
        tl = correlate(rec, log)
        assert [x.strategy for x in tl.steps] == ["watch-run", "watch-run"]
        assert ("value-mismatch" in tl.steps[0].flags) is (not expect_second)
        assert ("value-mismatch" in tl.steps[1].flags) is expect_second
        assert "sub-interval-collapse" in tl.steps[1].flags

    CCR = "wnd[0]/shellcont/shell:CurrentCellRow"
    REC_FVR_CCR_FVR = (
        'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8\n'
        'session.findById("wnd[0]/shellcont/shell").currentCellRow = 3\n'
        'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 16\n'
    )

    def test_leftover_collapse_through_other_watch_key(self):
        # #131 item 2: one sample changed both properties, the recording has
        # two FirstVisibleRow assignments around a CurrentCellRow one. The
        # third step's leftover must collapse onto FirstVisibleRow's last
        # observed change although the *previous row* is the CurrentCellRow one.
        fvr, ccr = self.KEY, self.CCR
        log = _log(
            (0.0, [], {fvr: "0", ccr: "0"}),
            (1.0, [fvr, ccr], {fvr: "8", ccr: "3"}),
        )
        tl = correlate(Recording.parse(self.REC_FVR_CCR_FVR), log)
        assert [s.strategy for s in tl.steps] == ["watch-run"] * 3
        assert [s.t_start for s in tl.steps] == [pytest.approx(1.0)] * 3
        assert "sub-interval-collapse" not in tl.steps[0].flags
        assert "sub-interval-collapse" in tl.steps[1].flags
        assert "sub-interval-collapse" in tl.steps[2].flags
        assert tl.strategy_counts == {"watch-run": 3}

    def test_leftover_not_collapsed_after_cursor_moved(self):
        # CurrentCellRow changed in a *later* sample than FirstVisibleRow, so
        # the missed third change happened after that: collapsing onto t=1.0
        # would timestamp it before an action known to be earlier.
        fvr, ccr = self.KEY, self.CCR
        log = _log(
            (0.0, [], {fvr: "0", ccr: "0"}),
            (1.0, [fvr], {fvr: "8", ccr: "0"}),
            (2.0, [ccr], {fvr: "8", ccr: "3"}),
        )
        tl = correlate(Recording.parse(self.REC_FVR_CCR_FVR), log)
        assert [s.strategy for s in tl.steps] == ["watch-run", "watch-run", "unmatched"]
        assert [s.t_start for s in tl.steps[:2]] == [pytest.approx(1.0), pytest.approx(2.0)]
        assert tl.steps[2].t_start is None

    @pytest.mark.parametrize(("third", "expect_mismatch"), [("16", True), ("8", False)])
    def test_leftover_value_mismatch_recomputed_through_other_watch_key(self, third, expect_mismatch):
        # ``value-mismatch`` is recomputed for the leftover row rather than
        # inherited from the row it collapses onto. Which sample it is computed
        # against is not observable here: the cursor guard makes the shared
        # sample and the cursor sample the same one.
        fvr, ccr = self.KEY, self.CCR
        rec = Recording.parse(
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8\n'
            'session.findById("wnd[0]/shellcont/shell").currentCellRow = 3\n'
            f'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = {third}\n'
        )
        log = _log(
            (0.0, [], {fvr: "0", ccr: "0"}),
            (1.0, [fvr, ccr], {fvr: "8", ccr: "3"}),
        )
        tl = correlate(rec, log)
        assert [s.strategy for s in tl.steps] == ["watch-run"] * 3
        assert "value-mismatch" not in tl.steps[0].flags
        assert ("value-mismatch" in tl.steps[2].flags) is expect_mismatch

    def test_leftover_not_collapsed_across_unmatched_step(self):
        # An unmatched press between the two FirstVisibleRow assignments
        # happened after the first one; collapsing the third step onto t=1.0
        # would timestamp it before an action known to be earlier.
        rec = Recording.parse(
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8\n'
            'session.findById("wnd[0]/tbar[1]/btn[8]").press\n'
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 16\n'
        )
        log = _log(
            (0.0, [], {self.KEY: "0"}),
            (1.0, [self.KEY], {self.KEY: "8"}),
        )
        tl = correlate(rec, log)
        assert [s.strategy for s in tl.steps] == ["watch-run", "unmatched", "unmatched"]
        assert tl.steps[2].t_start is None

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


def _j6(path):
    return load_monitor_log(Path(path).read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize("log_path", J6_PATHS)
class TestJourney6Acceptance:
    """The cross-journey pairing the prototype scored 17/18 on (#126).

    journey3_bp.vbs (committed) against the real journey-6 monitor log: the
    same task family, recorded on different runs — the layout variance the
    ddic-suffix strategy exists for.
    """

    def test_cross_journey_score(self, log_path):
        rec = Recording.load(SPIKE / "journey3_bp.vbs")
        tl = correlate(rec, _j6(log_path))
        counts = Counter(s.strategy for s in tl.steps)
        assert counts["exact-focus"] == 10
        assert counts["ddic-suffix"] == 3
        assert counts["modal-bracket"] == 2
        assert counts["fingerprint-screen"] == 1
        assert counts["fingerprint-title"] == 1
        assert counts["recorder-boilerplate"] == 1
        assert counts["unmatched"] == 0, counts
        assert sum(counts.values()) == 18

    def test_ddic_suffix_rows_are_pinned(self, log_path):
        # The counts alone do not prove *which* sample each suffix match landed on.
        tl = correlate(Recording.load(SPIKE / "journey3_bp.vbs"), _j6(log_path))
        rows = [s for s in tl.steps if s.strategy == "ddic-suffix"]
        assert [s.member for s in rows] == ["text", "setFocus", "caretPosition"]
        assert all(s.element_id.endswith("txtSZA11_0100-TEL_NUMBER") for s in rows)
        assert [s.t_start for s in rows] == [34.984] * 3
        assert all("layout-sensitive" in s.flags for s in rows)
        assert [("sub-interval-collapse" in s.flags) for s in rows] == [False, True, True]


@pytest.mark.parametrize("log_path", J6_PATHS)
class TestJourney6Modal:
    """Modal bracket and title fingerprint pinned on a real log.

    Every number below was read from the recorded log, not invented: a trim or
    loader regression that shifts a sample index or a clock value fails here.
    """

    def _steps(self, log_path):
        log = _j6(log_path)
        return log, correlate(Recording.load(SPIKE / "journey3_bp.vbs"), log).steps

    def test_title_fingerprint_step(self, log_path):
        log, steps = self._steps(log_path)
        (step,) = [s for s in steps if s.strategy == "fingerprint-title"]
        assert step.member == "press"
        assert step.element_id.endswith("tbar[1]/btn[5]")
        assert step.t_start == pytest.approx(13.484)
        # The anchor sample really is a wnd[0]:Text change, with the new title.
        (sample,) = [x for x in log.samples if x.elapsed == step.t_start]
        assert "wnd[0]:Text" in sample.changed
        assert sample.values["wnd[0]:Text"] == "Person anlegen"

    def test_modal_brackets_span_the_dialog(self, log_path):
        log, steps = self._steps(log_path)
        modal = [s for s in steps if s.strategy == "modal-bracket"]
        assert [s.element_id for s in modal] == ["wnd[1]/usr/btnBUTTON_1", "wnd[1]/usr/btnBUTTON_2"]
        assert {(s.t_start, s.t_end) for s in modal} == {(42.359, 47.593)}
        opened = next(x for x in log.samples if x.elapsed == 42.359)
        closed = next(x for x in log.samples if x.elapsed == 47.593)
        assert opened.values["wnd[1]:Text"] == "Warnung"
        assert closed.values["wnd[1]:Text"] == "<absent>"

    def test_no_status_bar_keys_so_no_sbar_text(self, log_path):
        # Journey 6 predates the status-bar sampling (#127): nothing to attribute.
        log, steps = self._steps(log_path)
        assert not any(k.startswith("sbar_") for x in log.samples for k in x.values)
        assert all(s.sbar_text is None for s in steps)


class TestSbarAndTranscript:
    A = f"{FOCUS}/wnd[0]/usr/ctxtGD-TAB"
    B = f"{FOCUS}/wnd[0]/usr/ctxtGD-MAX_LINES"
    REC_A = 'session.findById("wnd[0]/usr/ctxtGD-TAB").text = "T000"\n'
    REC_AB = REC_A + 'session.findById("wnd[0]/usr/ctxtGD-MAX_LINES").text = "500"\n'

    def test_sbar_message_after_anchor_belongs_to_this_step(self):
        # SAP writes the message *after* processing the step, so it lands one or
        # more samples after the step's anchor — and still belongs to that step.
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            (2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "A caused this"}),
            (3.0, ["focus_id"], {"focus_id": self.B, "sbar_text": "A caused this"}),
            (4.0, ["sbar_text"], {"focus_id": self.B, "sbar_text": "B caused this"}),
        )
        tl = correlate(Recording.parse(self.REC_AB), log)
        assert [s.strategy for s in tl.steps] == ["exact-focus", "exact-focus"]
        assert tl.steps[0].sbar_text == "A caused this"
        assert tl.steps[1].sbar_text == "B caused this"

    def test_sbar_change_on_next_anchor_sample_belongs_to_next_step(self):
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            (2.0, ["focus_id", "sbar_text"], {"focus_id": self.B, "sbar_text": "with B"}),
        )
        tl = correlate(Recording.parse(self.REC_AB), log)
        assert [s.t_start for s in tl.steps] == [pytest.approx(1.0), pytest.approx(2.0)]
        assert tl.steps[0].sbar_text is None
        assert tl.steps[1].sbar_text == "with B"

    def test_sbar_stale_message_before_anchor_is_not_attributed(self):
        # The text in force *at* the anchor is the previous step's (or the
        # baseline's) outcome, not this step's.
        log = _log(
            (0.5, ["sbar_text"], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": "4 Einträge gefunden"}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": "4 Einträge gefunden"}),
        )
        tl = correlate(Recording.parse(self.REC_A), log)
        assert tl.steps[0].t_start == pytest.approx(1.0)
        assert tl.steps[0].sbar_text is None

    @pytest.mark.parametrize("sentinel", ["<unreadable>", "<absent>"])
    @pytest.mark.parametrize("where", ["at-anchor", "after-anchor"])
    def test_sbar_sentinel_change_is_skipped(self, sentinel, where):
        # A failed read inside the window is not this step's outcome, and must
        # not fall back to the real value that was in force before the anchor.
        if where == "at-anchor":
            anchor = (1.0, ["focus_id", "sbar_text"], {"focus_id": self.A, "sbar_text": sentinel})
            tail = ()
        else:
            anchor = (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": "old msg"})
            tail = ((2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": sentinel}),)
        log = _log((0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": "old msg"}), anchor, *tail)
        tl = correlate(Recording.parse(self.REC_A), log)
        assert tl.steps[0].t_start == pytest.approx(1.0)
        assert tl.steps[0].sbar_text is None

    def test_sbar_key_absent_from_baseline_still_read(self):
        # A log whose status bar first became readable mid-run: the key is not
        # in sample 0, and the message must still be found.
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell"}),
            (1.0, ["focus_id"], {"focus_id": self.A}),
            (2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "late key"}),
        )
        tl = correlate(Recording.parse(self.REC_A), log)
        assert tl.steps[0].sbar_text == "late key"

    def test_sbar_shared_by_collapsed_steps(self):
        rec = Recording.parse(
            self.REC_A
            + 'session.findById("wnd[0]/usr/ctxtGD-TAB").setFocus\n'
            + 'session.findById("wnd[0]/usr/ctxtGD-TAB").caretPosition = 4\n'
        )
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            (2.0, [], {"focus_id": self.A, "sbar_text": ""}),
            (3.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "shared"}),
        )
        tl = correlate(rec, log)
        assert [s.t_start for s in tl.steps] == [pytest.approx(1.0)] * 3
        assert [s.sbar_text for s in tl.steps] == ["shared"] * 3

    def test_sbar_shared_by_keyboard_anchor_pair(self):
        # okcd text + sendVKey share one transition sample, hence one window.
        rec = Recording.parse(
            'session.findById("wnd[0]/tbar[0]/okcd").text = "/nse16n"\nsession.findById("wnd[0]").sendVKey 0\n'
        )
        log = _log(
            (0.0, [], {"transaction": "SESSION_MANAGER", "sbar_text": ""}),
            (1.0, ["transaction"], {"transaction": "SE16N", "sbar_text": ""}),
            (2.0, ["sbar_text"], {"transaction": "SE16N", "sbar_text": "SE16N entered"}),
        )
        tl = correlate(rec, log)
        assert [s.strategy for s in tl.steps] == ["fingerprint-screen"] * 2
        assert "keyboard-anchor" in tl.steps[1].flags
        assert [s.sbar_text for s in tl.steps] == ["SE16N entered"] * 2

    def test_sbar_shared_by_collapsed_fingerprint_presses(self):
        rec = Recording.parse('session.findById("wnd[0]").sendVKey 0\nsession.findById("wnd[0]").sendVKey 0\n')
        log = _log(
            (0.0, [], {"screen_number": 100, "sbar_text": ""}),
            (1.0, ["screen_number"], {"screen_number": 200, "sbar_text": ""}),
            (2.0, ["sbar_text"], {"screen_number": 200, "sbar_text": "two presses"}),
        )
        tl = correlate(rec, log)
        assert [s.strategy for s in tl.steps] == ["fingerprint-screen"] * 2
        assert "sub-interval-collapse" in tl.steps[1].flags
        assert [s.sbar_text for s in tl.steps] == ["two presses"] * 2

    def test_sbar_shared_by_leftover_watch_assignment(self):
        key = TestWatchRun.KEY
        rec = Recording.parse(
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8\n'
            'session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 16\n'
        )
        log = _log(
            (0.0, [], {key: "0", "sbar_text": ""}),
            (1.0, [key], {key: "8", "sbar_text": ""}),
            (2.0, ["sbar_text"], {key: "8", "sbar_text": "scrolled"}),
        )
        tl = correlate(rec, log)
        assert [s.strategy for s in tl.steps] == ["watch-run"] * 2
        assert "sub-interval-collapse" in tl.steps[1].flags
        assert [s.sbar_text for s in tl.steps] == ["scrolled"] * 2

    def test_sbar_last_step_window_runs_to_log_end(self):
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            *[(float(t), [], {"focus_id": self.A, "sbar_text": ""}) for t in range(2, 6)],
            (6.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "at the very end"}),
        )
        tl = correlate(Recording.parse(self.REC_A), log)
        assert tl.steps[0].sbar_text == "at the very end"

    def test_sbar_last_change_in_window_wins(self):
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            (2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "first"}),
            (3.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "second"}),
        )
        tl = correlate(Recording.parse(self.REC_A), log)
        assert tl.steps[0].sbar_text == "second"

    def test_sbar_cleared_bar_is_empty_string_not_none(self):
        # SAP clearing the bar is an observed change; "" and None must stay distinguishable.
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": "old msg"}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": "old msg"}),
            (2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": ""}),
        )
        tl = correlate(Recording.parse(self.REC_A), log)
        assert tl.steps[0].sbar_text == ""

    def test_sbar_none_for_unmatched_step(self):
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["sbar_text"], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": "noise"}),
        )
        tl = correlate(Recording.parse(self.REC_A), log)
        assert tl.steps[0].strategy == "unmatched"
        assert tl.steps[0].sbar_text is None

    def test_sbar_change_between_matched_steps_skips_unmatched_middle(self):
        # An unmatched step has no window, so it does not cut the earlier
        # matched step's window short: the message belongs to A.
        rec = Recording.parse(
            self.REC_A
            + 'session.findById("wnd[0]/tbar[1]/btn[8]").press\n'
            + 'session.findById("wnd[0]/usr/ctxtGD-MAX_LINES").text = "500"\n'
        )
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            (2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "between"}),
            (3.0, ["focus_id"], {"focus_id": self.B, "sbar_text": "between"}),
        )
        tl = correlate(rec, log)
        assert [s.strategy for s in tl.steps] == ["exact-focus", "unmatched", "exact-focus"]
        assert [s.sbar_text for s in tl.steps] == ["between", None, None]

    def test_sbar_reappearance_after_unreadable_is_not_a_change(self):
        # The monitor flags 'A msg' -> '<unreadable>' -> 'A msg' as two
        # changes; the second is the same readable message coming back, not
        # something B caused.
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            (2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "A msg"}),
            (3.0, ["focus_id"], {"focus_id": self.B, "sbar_text": "A msg"}),
            (4.0, ["sbar_text"], {"focus_id": self.B, "sbar_text": "<unreadable>"}),
            (5.0, ["sbar_text"], {"focus_id": self.B, "sbar_text": "A msg"}),
        )
        tl = correlate(Recording.parse(self.REC_AB), log)
        assert [s.strategy for s in tl.steps] == ["exact-focus", "exact-focus"]
        assert tl.steps[0].sbar_text == "A msg"
        assert tl.steps[1].sbar_text is None

    def test_sbar_reappearance_after_absent_is_a_change(self):
        # Unlike <unreadable>, <absent> is a real state the monitor never
        # carries forward (monitor._carry_forward_unreadable): the bar was
        # gone, so the same text showing up again is a new message.
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell", "sbar_text": ""}),
            (1.0, ["focus_id"], {"focus_id": self.A, "sbar_text": ""}),
            (2.0, ["sbar_text"], {"focus_id": self.A, "sbar_text": "A msg"}),
            (3.0, ["focus_id"], {"focus_id": self.B, "sbar_text": "A msg"}),
            (4.0, ["sbar_text"], {"focus_id": self.B, "sbar_text": "<absent>"}),
            (5.0, ["sbar_text"], {"focus_id": self.B, "sbar_text": "A msg"}),
        )
        tl = correlate(Recording.parse(self.REC_AB), log)
        assert [s.strategy for s in tl.steps] == ["exact-focus", "exact-focus"]
        assert tl.steps[0].sbar_text == "A msg"
        assert tl.steps[1].sbar_text == "A msg"

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

    def test_markdown_warns_about_transcript_origin_only_with_transcript(self):
        # Mutation: drop the preamble line (or emit it unconditionally) and one of these fails.
        rec = Recording.parse(self_REC)
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell"}),
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/okcd"}),
        )
        with_cue = correlate(rec, log, transcript=(TranscriptEntry(0.9, 1.1, "typing the transaction"),))
        assert "assume the narration started together with the recorder (unverified)" in with_cue.to_markdown()
        assert "unverified" not in self._tl().to_markdown()
        # A transcript was given, but no cue lands on any step: the table shows
        # no excerpt, so the caveat would describe nothing on the page.
        no_hit = correlate(rec, log, transcript=(TranscriptEntry(100.0, 101.0, "long after the journey"),))
        assert all(not s.transcript for s in no_hit.steps)
        assert "unverified" not in no_hit.to_markdown()

    def test_jsonl_marks_transcript_origin_assumed(self):
        rec = Recording.parse(self_REC)
        log = _log(
            (0.0, [], {"focus_id": f"{FOCUS}/wnd[0]/shellcont/shell"}),
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/tbar[0]/okcd"}),
        )
        with_t = correlate(rec, log, transcript=(TranscriptEntry(100.0, 101.0, "no step here"),))
        rows = [json.loads(line) for line in with_t.to_jsonl().splitlines()]
        assert rows and all(r["transcript_origin_assumed"] is True for r in rows)
        rows = [json.loads(line) for line in self._tl().to_jsonl().splitlines()]
        assert rows and all(r["transcript_origin_assumed"] is False for r in rows)


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

    def test_cli_help_states_transcript_origin_assumption(self):
        CliRunner = pytest.importorskip("typer.testing").CliRunner

        from sapsucker.correlate_cli import app

        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0, result.output
        # rich wraps the help inside a box and, on CI, colours it: drop ANSI
        # escapes and the borders, normalise whitespace.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        text = " ".join(plain.replace("│", " ").replace("|", " ").split())
        assert "unverified" in text
        assert "started together with the recorder" in text

    @pytest.mark.parametrize("with_transcript", [True, False])
    def test_cli_transcript_prints_origin_notice(self, tmp_path, with_transcript):
        # The JSONL has no preamble, so without -m the caveat must reach the
        # user some other way: a one-line stderr notice whenever -t is given.
        CliRunner = pytest.importorskip("typer.testing").CliRunner
        from sapsucker.correlate_cli import app

        srt = tmp_path / "n.srt"
        srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhallo\n", encoding="utf-8")
        args = [
            str(SPIKE / "journey5_bp.vbs"),
            str(SPIKE / "journey5_timing.jsonl"),
            "--out",
            str(tmp_path / "t.jsonl"),
        ]
        if with_transcript:
            args += ["--transcript", str(srt)]
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
        notice = "transcript cues assume the narration started together with the recorder (unverified)"
        assert (notice in " ".join(result.stderr.split())) is with_transcript
        assert notice not in result.stdout

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

    @pytest.mark.parametrize("content", ["", "\n\n", "﻿", "1\n2\n"])
    def test_cli_cue_less_transcript_exits_2(self, tmp_path, content):
        # -t promises transcript_origin_assumed: true on every record; a file
        # with no cues would silently write false, so it is rejected instead.
        CliRunner = pytest.importorskip("typer.testing").CliRunner

        from sapsucker.correlate_cli import app

        empty = tmp_path / "empty.srt"
        empty.write_text(content, encoding="utf-8")
        out = tmp_path / "t.jsonl"
        args = [str(SPIKE / "journey5_bp.vbs"), str(SPIKE / "journey5_timing.jsonl"), "-t", str(empty), "-o", str(out)]
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 2, result.output
        assert "bad --transcript: SRT has no cues" in " ".join(result.stderr.split())
        assert not out.exists()

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

    def test_trailing_caret_position_does_not_block_the_repeat(self):
        # setFocus/caretPosition never consume a focus event, so the recorder's
        # usual ".text = ..." + ".caretPosition = n" must not starve the repeat.
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/usr/txtA").caretPosition = 1\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 3.0, 3.0]

    def test_suffix_repeat_counts_later_steps_by_suffix(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtS1-TEL").text = "1"\n'
            'session.findById("wnd[0]/usr/txtS1-TEL").text = "2"\n'
            'session.findById("wnd[0]/usr/txtS9-TEL").text = "3"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtS2-TEL"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtS2-TEL"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 3.0]

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

    @pytest.mark.parametrize(
        "cue",
        [
            "00:00:20,000 --> 00:00:10,000",
            "00:61:00,000 --> 00:62:00,000",
            "00:00:61,000 --> 00:00:62,000",
        ],
    )
    def test_parse_srt_rejects_invalid_intervals(self, cue):
        from sapsucker.correlate_cli import parse_srt

        with pytest.raises(ValueError, match="SRT"):
            parse_srt(f"1\n{cue}\ntext\n")


class TestMatcherFollowUps:
    """Matcher items from #131."""

    def test_repeat_after_an_unmatched_step_is_not_silently_shared(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtZ").text = "z"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert [x.strategy for x in tl.steps] == ["exact-focus", "unmatched", "exact-focus"]
        assert tl.steps[2].t_start == 3.0

    def test_repeat_after_an_unmatched_step_with_one_event_is_flagged(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtZ").text = "z"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
        )
        log = _log((1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}))
        tl = correlate(rec, log)
        assert "sub-interval-collapse" in tl.steps[2].flags

    def test_two_watched_properties_changing_in_one_sample_both_match(self):
        shell = "wnd[0]/shellcont/shell"
        rec = Recording.parse(
            f'session.findById("{shell}").firstVisibleRow = 8\nsession.findById("{shell}").currentCellRow = 3\n'
        )
        log = _log(
            (1.0, [], {f"{shell}:FirstVisibleRow": "0", f"{shell}:CurrentCellRow": "0"}),
            (
                2.0,
                [f"{shell}:FirstVisibleRow", f"{shell}:CurrentCellRow"],
                {f"{shell}:FirstVisibleRow": "8", f"{shell}:CurrentCellRow": "3"},
            ),
        )
        tl = correlate(rec, log)
        assert [(x.strategy, x.t_start) for x in tl.steps] == [("watch-run", 2.0), ("watch-run", 2.0)]
        assert "sub-interval-collapse" not in tl.steps[0].flags
        assert "sub-interval-collapse" in tl.steps[1].flags
        assert not any("value-mismatch" in x.flags for x in tl.steps)

    def test_visited_field_edited_later_does_not_block_the_repeat_while_events_remain(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/usr/txtC").text = "c"\n'
            'session.findById("wnd[0]/usr/txtB").text = "b"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (4.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtC"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 3.0, 4.0, 5.0]
        assert not any("sub-interval-collapse" in x.flags for x in tl.steps)

    def test_unrelated_field_with_the_same_suffix_does_not_block_the_repeat(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtP").text = "1"\n'
            'session.findById("wnd[0]/usr/txtP").text = "2"\n'
            'session.findById("wnd[0]/usr/ctxtX-KUNNR").text = "k"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtP"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtY-KUNNR"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtP"}),
            (4.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtX-KUNNR"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 3.0, 4.0]

    def test_transcript_window_is_widened_on_both_sides_of_a_modal_bracket(self):
        rec = Recording.parse('session.findById("wnd[1]/usr/btnA").press\n')
        log = _log(
            (1.0, [], {"wnd[1]:Text": "<absent>"}),
            (4.0, ["wnd[1]:Text"], {"wnd[1]:Text": "Dialog"}),
            (5.0, ["wnd[1]:Text"], {"wnd[1]:Text": "<absent>"}),
        )
        cues = (
            TranscriptEntry(0.5, 1.9, "before"),
            TranscriptEntry(0.5, 2.1, "overlaps lower bound"),
            TranscriptEntry(6.9, 7.5, "overlaps upper bound"),
            TranscriptEntry(7.1, 7.5, "after"),
        )
        step = correlate(rec, log, transcript=cues).steps[0]
        assert (step.t_start, step.t_end) == (4.0, 5.0)
        assert step.transcript == ("overlaps lower bound", "overlaps upper bound")

    def test_repeat_does_not_jump_over_a_visited_field_a_later_step_needs(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/usr/txtB").text = "b"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 2.0]
        assert "sub-interval-collapse" in tl.steps[1].flags

    def test_repeat_does_not_starve_a_later_step_whose_only_event_comes_after_anothers(self):
        # B's only remaining event (B@5) is after C's (C@4): taking A@3 for the
        # repeat would move the cursor past C and leave it unmatched.
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/usr/txtB").text = "b"\n'
            'session.findById("wnd[0]/usr/txtC").text = "c"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (4.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtC"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 2.0, 4.0]
        assert "unmatched" not in [x.strategy for x in tl.steps]

    def test_suffix_repeat_after_an_unmatched_step_is_recognised(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/ctxtX-KUNNR").text = "1"\n'
            'session.findById("wnd[0]/usr/txtZ").text = "z"\n'
            'session.findById("wnd[0]/usr/ctxtY-KUNNR").text = "2"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtQ-KUNNR"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtQ-KUNNR"}),
        )
        tl = correlate(rec, log)
        assert [x.strategy for x in tl.steps] == ["ddic-suffix", "unmatched", "ddic-suffix"]
        assert tl.steps[2].t_start == 3.0

    def test_change_listed_in_the_baseline_sample_is_not_a_watch_match(self):
        shell = "wnd[0]/shellcont/shell"
        key = f"{shell}:FirstVisibleRow"
        rec = Recording.parse(f'session.findById("{shell}").firstVisibleRow = 8\n')
        log = _log((0.0, [key], {key: "8"}), (1.0, [key], {key: "8"}))
        assert correlate(rec, log).steps[0].t_start == 1.0

    def test_replay_falls_back_to_suffix_events_once_exact_events_are_exhausted(self):
        # X-KUNNR has an exact event only *before* the cursor; its match comes from
        # the suffix event at 3.0. Taking P@4.0 for the repeat would strand it.
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtP").text = "1"\n'
            'session.findById("wnd[0]/usr/txtP").text = "2"\n'
            'session.findById("wnd[0]/usr/ctxtX-KUNNR").text = "k"\n'
            'session.findById("wnd[0]/usr/txtC").text = "c"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtX-KUNNR"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtP"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/ctxtY-KUNNR"}),
            (4.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtP"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtC"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [2.0, 2.0, 3.0, 5.0]
        assert "unmatched" not in [x.strategy for x in tl.steps]

    def test_repeat_collapses_when_a_later_step_is_anchored_by_something_other_than_focus(self):
        # The press is timestamped by a screen change the focus replay cannot see;
        # jumping to A@3.0 would move the cursor past it and strand B.
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/tbar[0]/btn[11]").press\n'
            'session.findById("wnd[0]/usr/txtB").text = "b"\n'
        )
        a = f"{FOCUS}/wnd[0]/usr/txtA"
        b = f"{FOCUS}/wnd[0]/usr/txtB"
        log = _log(
            (1.0, ["focus_id"], {"focus_id": a, "screen_number": 100}),
            (1.5, ["screen_number"], {"focus_id": a, "screen_number": 200}),
            (2.0, ["focus_id"], {"focus_id": b, "screen_number": 200}),
            (3.0, ["focus_id"], {"focus_id": a, "screen_number": 200}),
            (4.0, ["focus_id"], {"focus_id": b, "screen_number": 200}),
            (5.0, ["screen_number"], {"focus_id": b, "screen_number": 300}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 1.5, 2.0]
        assert "unmatched" not in [x.strategy for x in tl.steps]

    def test_repeat_collapses_when_a_later_step_is_a_set_focus(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
            'session.findById("wnd[0]/usr/txtB").setFocus\n'
            'session.findById("wnd[0]/usr/txtC").text = "c"\n'
            'session.findById("wnd[0]/usr/txtB").text = "b"\n'
        )
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (4.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtC"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, 1.0, 2.0, 4.0, 5.0]
        assert "unmatched" not in [x.strategy for x in tl.steps]

    @pytest.mark.parametrize(
        "statements",
        [
            ["A", "A", "B", "B", "C"],
            ["A", "A", "A", "B", "C"],
        ],
    )
    def test_repeat_collapses_when_later_steps_repeat_a_field(self, statements):
        rec = Recording.parse("".join(f'session.findById("wnd[0]/usr/txt{f}").text = "x"\n' for f in statements))
        log = _log(
            (1.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (2.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
            (3.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtA"}),
            (4.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtC"}),
            (5.0, ["focus_id"], {"focus_id": f"{FOCUS}/wnd[0]/usr/txtB"}),
        )
        tl = correlate(rec, log)
        assert "unmatched" not in [x.strategy for x in tl.steps]
        assert tl.steps[1].t_start == 1.0

    def test_caret_position_after_an_unmatched_step_still_inherits_the_edit(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/txtA").text = "1"\n'
            'session.findById("wnd[0]/usr/txtZ").text = "z"\n'
            'session.findById("wnd[0]/usr/txtA").caretPosition = 1\n'
            'session.findById("wnd[0]/usr/txtA").text = "2"\n'
        )
        a = f"{FOCUS}/wnd[0]/usr/txtA"
        log = _log(
            (1.0, ["focus_id"], {"focus_id": a}),
            (3.0, ["focus_id"], {"focus_id": a}),
            (5.0, ["focus_id"], {"focus_id": a}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, None, 1.0, 3.0]
        assert "sub-interval-collapse" in tl.steps[2].flags

    def test_caret_position_after_an_unmatched_step_inherits_on_the_suffix_path(self):
        rec = Recording.parse(
            'session.findById("wnd[0]/usr/ctxtX-KUNNR").text = "1"\n'
            'session.findById("wnd[0]/usr/txtZ").text = "z"\n'
            'session.findById("wnd[0]/usr/ctxtX-KUNNR").caretPosition = 1\n'
            'session.findById("wnd[0]/usr/ctxtX-KUNNR").text = "2"\n'
        )
        q = f"{FOCUS}/wnd[0]/usr/ctxtQ-KUNNR"
        log = _log(
            (1.0, ["focus_id"], {"focus_id": q}),
            (3.0, ["focus_id"], {"focus_id": q}),
            (5.0, ["focus_id"], {"focus_id": q}),
        )
        tl = correlate(rec, log)
        assert [x.t_start for x in tl.steps] == [1.0, None, 1.0, 3.0]


class TestCliFailureModes:
    """The CLI maps every input and output failure to a diagnostic and exit code 2 (#131)."""

    def _invoke(self, *args):
        CliRunner = pytest.importorskip("typer.testing").CliRunner
        from sapsucker.correlate_cli import app

        return CliRunner().invoke(app, [str(a) for a in args])

    def test_unreadable_recording_exits_2(self, tmp_path, monkeypatch):
        from sapsucker import correlate_cli

        def boom(path):
            raise OSError("disk on fire")

        monkeypatch.setattr(correlate_cli.Recording, "load", staticmethod(boom))
        result = self._invoke(SPIKE / "journey5_bp.vbs", SPIKE / "journey5_timing.jsonl", "--out", tmp_path / "t.jsonl")
        assert result.exit_code == 2
        assert "bad recording: disk on fire" in result.output

    def test_unreadable_monitor_log_exits_2(self, tmp_path, monkeypatch):
        log = tmp_path / "unreadable.jsonl"
        log.write_text("{}\n", encoding="utf-8")
        real = Path.read_text

        def read_text(self, *args, **kwargs):
            if self.name == "unreadable.jsonl":
                raise OSError("disk on fire")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
        result = self._invoke(SPIKE / "journey5_bp.vbs", log, "--out", tmp_path / "t.jsonl")
        assert result.exit_code == 2
        assert "bad monitor log: disk on fire" in result.output

    def test_unwritable_out_exits_2(self, tmp_path):
        result = self._invoke(
            SPIKE / "journey5_bp.vbs", SPIKE / "journey5_timing.jsonl", "--out", tmp_path
        )  # a directory
        assert result.exit_code == 2, result.output
        assert "cannot write --out" in result.output

    def test_unwritable_markdown_exits_2(self, tmp_path):
        result = self._invoke(
            SPIKE / "journey5_bp.vbs",
            SPIKE / "journey5_timing.jsonl",
            "--out",
            tmp_path / "t.jsonl",
            "--markdown",
            tmp_path,
        )
        assert result.exit_code == 2, result.output
        assert "cannot write --markdown" in result.output


class TestFingerprintPrecedence:
    """The first observable event after a press wins: a screen change or a title change (#131)."""

    REC = 'session.findById("wnd[0]/tbar[0]/btn[11]").press\n'

    def _log(self, *events):
        # events: (elapsed, kind) with kind "screen" (screen_number changes) or "title" (title only).
        rows = [(1.0, [], {"wnd[0]:Text": "T0", "screen_number": 100})]
        screen, title = 100, 0
        for elapsed, kind in events:
            if kind == "screen":
                screen += 100
                rows.append((elapsed, ["screen_number"], {"wnd[0]:Text": f"T{title}", "screen_number": screen}))
            else:
                title += 1
                rows.append((elapsed, ["wnd[0]:Text"], {"wnd[0]:Text": f"T{title}", "screen_number": screen}))
        return _log(*rows)

    def test_screen_change_before_title_change_wins(self):
        tl = correlate(Recording.parse(self.REC), self._log((2.0, "screen"), (3.0, "title")))
        assert (tl.steps[0].strategy, tl.steps[0].t_start) == ("fingerprint-screen", 2.0)

    def test_title_change_before_screen_change_wins(self):
        tl = correlate(Recording.parse(self.REC), self._log((2.0, "title"), (3.0, "screen")))
        assert (tl.steps[0].strategy, tl.steps[0].t_start) == ("fingerprint-title", 2.0)

    # A sample cannot be both: `_title_transition` excludes samples where the screen
    # changed, so `screen_idx <= title_idx` and `<` are equivalent (the tie is unreachable).
