"""Integration tests for the correlator against a live SAP GUI session.

Runs the real pipeline end-to-end: navigate SE16N by script (the recorded
journey, played directly), while a :class:`SessionMonitor` samples the same
session; then correlate the emitted ``.vbs``-equivalent steps against the
JSONL. All steps are read-only. Skips silently off-Windows / on CI / without
credentials, like every other integration test here.
"""

from __future__ import annotations

import sys
import time

import pytest

from unittests.conftest import is_sap_integration_test_machine

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="SAP GUI COM is Windows-only"),
    pytest.mark.skipif(
        not is_sap_integration_test_machine(),
        reason="SAP integration tests only run on authorized machines",
    ),
]


class TestLiveCorrelation:
    def test_se16n_journey_correlates(self, sap_desktop_session):
        """Play a small SE16N journey and correlate it against a live monitor run."""
        from sapsucker._correlate import correlate, load_monitor_log
        from sapsucker._recording import Recording
        from sapsucker.monitor import SessionMonitor, Watch

        session = sap_desktop_session

        # The journey, exactly as the recorder would have written it.
        journey = Recording.parse(
            'session.findById("wnd[0]").resizeWorkingPane 152,33,false\n'
            'session.findById("wnd[0]/tbar[0]/okcd").text = "/nse16n"\n'
            'session.findById("wnd[0]").sendVKey 0\n'
            'session.findById("wnd[0]/usr/ctxtGD-TAB").text = "T000"\n'
            'session.findById("wnd[0]/usr/ctxtGD-TAB").setFocus\n'
            'session.findById("wnd[0]/usr/ctxtGD-TAB").caretPosition = 4\n'
            'session.findById("wnd[0]").sendVKey 8\n'
        )

        grid_id = "wnd[0]/shellcont/shell"
        monitor = SessionMonitor(
            session,
            watches=[Watch(element_id=grid_id, prop="FirstVisibleRow")],
            interval=0.05,
        )

        # COM is STA: everything touching the session happens on this thread.
        # Play one step, then pump the monitor generator a few rounds — the
        # caller owns the loop, so interleaving is the supported usage.
        samples = []
        gen = monitor.samples()

        def _play(step):
            element = session.find_by_id(step.element_id)
            if step.member == "setFocus":
                element.set_focus()
            elif step.args is None:
                getattr(element, step.member)()
            elif step.member == "sendVKey":
                element.send_v_key(int(step.args[0]))
            elif step.member == "caretPosition":
                element.caret_position = int(step.args[0])
            else:
                setattr(element, step.member, step.args[0])

        for step in journey.steps:
            if step.member != "resizeWorkingPane":
                _play(step)
            time.sleep(0.15)
            for _ in range(6):
                samples.append(next(gen))
        for _ in range(10):  # tail: let the final transition land
            samples.append(next(gen))

        log = load_monitor_log([__import__("json").dumps(s.as_record(), ensure_ascii=False) for s in samples])
        tl = correlate(journey, log)

        counts = tl.strategy_counts
        # Every real step must be timestamped (live-verified 2026-10-04: the
        # okcd pair shares one keyboard-anchor transition; GD-TAB gets
        # exact-focus; the trailing sendVKey 8 fingerprints the screen change).
        assert counts.get("unmatched", 0) == 0, f"unmatched steps: {counts}"
        tab = next(s for s in tl.steps if s.member == "text" and s.args == ("T000",))
        assert tab.strategy in ("exact-focus", "ddic-suffix"), tab
        assert tab.t_start is not None
        okcd_text = next(s for s in tl.steps if s.element_id.endswith("okcd"))
        assert okcd_text.strategy == "fingerprint-screen"
        assert "keyboard-anchor" in okcd_text.flags
        assert okcd_text.t_start is not None
        # The trailing sendVKey 8 (execute) binds to the screen transition.
        execute = tl.steps[-1]
        assert execute.member == "sendVKey"
        assert execute.strategy == "fingerprint-screen", execute
        assert execute.t_start is not None

        # Clean up: leave the session on a neutral screen.
        okcd_field = session.find_by_id("wnd[0]/tbar[0]/okcd")
        okcd_field.text = "/n"
        session.find_by_id("wnd[0]").send_v_key(0)
