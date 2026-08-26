"""Tests for sapsucker.monitor — no SAP required, all fakes."""

from itertools import islice
from typing import Any
from unittest.mock import MagicMock

import pytest

from sapsucker.monitor import ABSENT, UNREADABLE, Sample, SessionMonitor, Watch


class FakeSession:
    """A session whose observable state can be scripted per sample.

    ``info`` is the first thing SessionMonitor reads each sample, so advancing
    the script there keeps every read within one sample consistent. Advancing on
    every COM access instead would let watches see the *next* state while the
    rest of the sample saw the current one.
    """

    def __init__(self, states: list[dict[str, Any]]) -> None:
        self._states = states
        self._index = -1

    def _state(self) -> dict[str, Any]:
        # Hold the last state once the script runs out, so extra samples are stable.
        return self._states[min(max(self._index, 0), len(self._states) - 1)]

    @property
    def info(self) -> Any:
        self._index += 1
        state = self._state()
        info = MagicMock()
        info.transaction = state.get("transaction", "SE16N")
        info.program = state.get("program", "SAPLSETB")
        info.screen_number = state.get("screen_number", 200)
        return info

    @property
    def busy(self) -> bool:
        return bool(self._state().get("busy", False))

    def find_by_id(self, element_id: str, raise_error: bool = True) -> Any:
        state = self._state()
        if element_id == "wnd[0]":
            window = MagicMock()
            focus = state.get("focus")
            if focus is None:
                return None
            if focus == "raise":
                type(window.com.GuiFocus).Id = property(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
                return window
            window.com.GuiFocus.Id = focus
            return window
        if element_id in state.get("elements", {}):
            element = MagicMock()
            element.com.FirstVisibleRow = state["elements"][element_id]
            return element
        return None


def _take(monitor: SessionMonitor, n: int) -> list[Sample]:
    return list(islice(monitor.samples(), n))


class TestChangeDetection:
    def test_first_sample_is_baseline_with_no_changes(self):
        monitor = SessionMonitor(FakeSession([{"focus": "a"}]), interval=0)
        assert _take(monitor, 1)[0].changed == ()

    def test_reports_only_the_keys_that_moved(self):
        session = FakeSession([{"focus": "a"}, {"focus": "b"}])
        samples = _take(SessionMonitor(session, interval=0), 2)
        assert samples[1].changed == ("focus_id",)

    def test_screen_transition_reports_every_moved_key(self):
        session = FakeSession(
            [
                {"focus": "a", "transaction": "SE16N", "screen_number": 200},
                {"focus": "b", "transaction": "BP", "screen_number": 3000},
            ]
        )
        samples = _take(SessionMonitor(session, interval=0), 2)
        assert set(samples[1].changed) == {"transaction", "screen_number", "focus_id"}

    def test_unchanged_sample_reports_nothing_but_is_still_yielded(self):
        session = FakeSession([{"focus": "a"}, {"focus": "a"}])
        samples = _take(SessionMonitor(session, interval=0), 2)
        assert len(samples) == 2 and samples[1].changed == ()


class TestPauseSignal:
    def test_gap_is_none_when_nothing_changed(self):
        session = FakeSession([{"focus": "a"}, {"focus": "a"}])
        assert _take(SessionMonitor(session, interval=0), 2)[1].gap_since_change_s is None

    def test_gap_is_set_when_something_changed(self):
        session = FakeSession([{"focus": "a"}, {"focus": "b"}])
        gap = _take(SessionMonitor(session, interval=0), 2)[1].gap_since_change_s
        assert gap is not None and gap >= 0


class TestSentinels:
    def test_failed_read_carries_the_last_good_value_forward(self):
        """A mid-transition failure is not a change — that produced phantom events."""
        session = FakeSession([{"focus": "a"}, {"focus": "raise"}])
        samples = _take(SessionMonitor(session, interval=0), 2)
        assert samples[1].values["focus_id"] == "a"
        assert "focus_id" not in samples[1].changed

    def test_absent_element_is_reported_and_never_carried_forward(self):
        """A modal closing must be visible; masking it would hide the transition."""
        watch = Watch("wnd[1]", "FirstVisibleRow")
        session = FakeSession(
            [
                {"focus": "a", "elements": {"wnd[1]": 5}},
                {"focus": "a", "elements": {}},
            ]
        )
        samples = _take(SessionMonitor(session, watches=[watch], interval=0), 2)
        assert samples[0].values[watch.key] == "5"
        assert samples[1].values[watch.key] == ABSENT
        assert watch.key in samples[1].changed

    def test_unreadable_on_the_very_first_sample_is_kept(self):
        session = FakeSession([{"focus": "raise"}])
        assert _take(SessionMonitor(session, interval=0), 1)[0].values["focus_id"] == UNREADABLE


class TestWatches:
    def test_watch_reads_the_named_property(self):
        watch = Watch("wnd[0]/shellcont/shell", "FirstVisibleRow")
        session = FakeSession([{"focus": "a", "elements": {"wnd[0]/shellcont/shell": 470}}])
        assert _take(SessionMonitor(session, watches=[watch], interval=0), 1)[0].values[watch.key] == "470"

    def test_scroll_change_is_detected(self):
        watch = Watch("wnd[0]/shellcont/shell", "FirstVisibleRow")
        session = FakeSession(
            [
                {"focus": "a", "elements": {"wnd[0]/shellcont/shell": 1}},
                {"focus": "a", "elements": {"wnd[0]/shellcont/shell": 8}},
            ]
        )
        samples = _take(SessionMonitor(session, watches=[watch], interval=0), 2)
        assert samples[1].changed == (watch.key,)

    def test_multiple_watches_are_bound_separately(self):
        """A late-bound loop variable would make every watch read the last one."""
        watches = [Watch("wnd[0]/a", "FirstVisibleRow"), Watch("wnd[0]/b", "FirstVisibleRow")]
        session = FakeSession([{"focus": "x", "elements": {"wnd[0]/a": 1, "wnd[0]/b": 2}}])
        values = _take(SessionMonitor(session, watches=watches, interval=0), 1)[0].values
        assert values["wnd[0]/a:FirstVisibleRow"] == "1"
        assert values["wnd[0]/b:FirstVisibleRow"] == "2"


class TestWatchParse:
    def test_splits_on_the_last_colon_so_bdt_paths_survive(self):
        """BDT element ids contain colons: subA02P01:SAPLBUD0:1130."""
        watch = Watch.parse("wnd[0]/usr/subA02P01:SAPLBUD0:1130/cmbX:Text")
        assert watch.element_id == "wnd[0]/usr/subA02P01:SAPLBUD0:1130/cmbX"
        assert watch.prop == "Text"

    def test_key_round_trips(self):
        assert Watch.parse("wnd[1]:Text").key == "wnd[1]:Text"

    @pytest.mark.parametrize("raw", ["wnd[1]", "", ":Text", "wnd[1]:"])
    def test_rejects_malformed(self, raw):
        with pytest.raises(ValueError, match="element_id:ComProperty"):
            Watch.parse(raw)


class TestRecord:
    def test_as_record_inlines_values_next_to_metadata(self):
        session = FakeSession([{"focus": "a", "transaction": "BP"}])
        record = _take(SessionMonitor(session, interval=0), 1)[0].as_record()
        assert record["seq"] == 0
        assert record["changed"] == []
        assert record["transaction"] == "BP"
        assert record["focus_id"] == "a"
        assert "at" in record and "elapsed_s" in record

    def test_sequence_numbers_increment(self):
        session = FakeSession([{"focus": "a"}])
        assert [s.seq for s in _take(SessionMonitor(session, interval=0), 3)] == [0, 1, 2]
