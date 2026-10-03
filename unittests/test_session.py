"""Tests for GuiSession and GuiSessionInfo components."""

from unittest.mock import MagicMock

import pytest

from sapsucker.components.base import GuiContainer
from sapsucker.components.session import GuiSession, GuiSessionInfo
from unittests.conftest import make_mock_com


class TestGuiSessionInheritance:
    def test_extends_gui_container(self):
        assert issubclass(GuiSession, GuiContainer)

    def test_instance_is_gui_container(self):
        com = make_mock_com()
        session = GuiSession(com)
        assert isinstance(session, GuiContainer)


class TestGuiSessionProperties:
    def test_info_returns_gui_session_info(self):
        info_com = MagicMock()
        info_com.SystemName = "DEV"
        info_com.Client = "100"
        info_com.User = "TESTUSER"
        info_com.Transaction = "SE38"
        com = make_mock_com(Info=info_com)
        session = GuiSession(com)
        info = session.info
        assert isinstance(info, GuiSessionInfo)
        assert info.system_name == "DEV"
        assert info.client == "100"
        assert info.user == "TESTUSER"
        assert info.transaction == "SE38"

    def test_busy(self):
        com = make_mock_com(Busy=False)
        session = GuiSession(com)
        assert session.busy is False

    def test_active_window(self):
        window = make_mock_com(type_as_number=21, type_name="GuiMainWindow")
        com = make_mock_com(ActiveWindow=window)
        session = GuiSession(com)
        result = session.active_window
        assert result.com is window


class TestGuiSessionMethods:
    def test_create_session(self):
        com = make_mock_com()
        session = GuiSession(com)
        session.create_session()
        com.CreateSession.assert_called_once()

    def test_end_transaction(self):
        com = make_mock_com()
        session = GuiSession(com)
        session.end_transaction()
        com.EndTransaction.assert_called_once()

    def test_send_command(self):
        com = make_mock_com()
        session = GuiSession(com)
        session.send_command("/nSE38")
        com.SendCommand.assert_called_once_with("/nSE38")

    def test_send_command_async(self):
        com = make_mock_com()
        session = GuiSession(com)
        session.send_command_async("/nSE38")
        com.SendCommandAsync.assert_called_once_with("/nSE38")

    def test_lock_session_ui(self):
        com = make_mock_com()
        session = GuiSession(com)
        session.lock_session_ui()
        com.LockSessionUI.assert_called_once()

    def test_unlock_session_ui(self):
        com = make_mock_com()
        session = GuiSession(com)
        session.unlock_session_ui()
        com.UnlockSessionUI.assert_called_once()

    def test_get_v_key_description(self):
        com = make_mock_com()
        com.GetVKeyDescription.return_value = "Enter"
        session = GuiSession(com)
        assert session.get_v_key_description(0) == "Enter"
        com.GetVKeyDescription.assert_called_once_with(0)

    def test_get_object_tree(self):
        com = make_mock_com()
        session = GuiSession(com)
        session.get_object_tree("usr")
        com.GetObjectTree.assert_called_once_with("usr")


class TestGuiSessionInfo:
    def _make_info(self, **overrides):
        com = MagicMock()
        defaults = {
            "SystemName": "DEV",
            "Client": "100",
            "User": "TESTUSER",
            "Language": "EN",
            "Transaction": "SE38",
            "Program": "SAPMSSY0",
            "ScreenNumber": 100,
            "ApplicationServer": "sapserver01",
            "ResponseTime": 42,
            "RoundTrips": 3,
            "SessionNumber": 0,
            "SystemNumber": 0,
            "Codepage": 4110,
            "Flushes": 5,
            "Group": "PUBLIC",
            "MessageServer": "msgserver01",
            "SystemSessionId": "abc123",
            "IsLowSpeedConnection": False,
            "ScriptingModeReadOnly": False,
            "ScriptingModeRecordingDisabled": False,
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            setattr(com, key, value)
        return GuiSessionInfo(com)

    def test_system_name(self):
        assert self._make_info(SystemName="PRD").system_name == "PRD"

    def test_client(self):
        assert self._make_info(Client="200").client == "200"

    def test_user(self):
        assert self._make_info(User="ADMIN").user == "ADMIN"

    def test_language(self):
        assert self._make_info(Language="DE").language == "DE"

    def test_transaction(self):
        assert self._make_info(Transaction="SM37").transaction == "SM37"

    def test_program(self):
        assert self._make_info(Program="RSBTCDEL").program == "RSBTCDEL"

    def test_screen_number(self):
        assert self._make_info(ScreenNumber=200).screen_number == 200

    def test_application_server(self):
        assert self._make_info(ApplicationServer="app01").application_server == "app01"

    def test_response_time(self):
        assert self._make_info(ResponseTime=100).response_time == 100

    def test_round_trips(self):
        assert self._make_info(RoundTrips=7).round_trips == 7

    def test_session_number(self):
        assert self._make_info(SessionNumber=2).session_number == 2

    def test_system_number(self):
        assert self._make_info(SystemNumber=1).system_number == 1

    def test_codepage(self):
        assert self._make_info(Codepage=1100).codepage == 1100

    def test_flushes(self):
        assert self._make_info(Flushes=10).flushes == 10

    def test_group(self):
        assert self._make_info(Group="SPACE").group == "SPACE"

    def test_message_server(self):
        assert self._make_info(MessageServer="msg01").message_server == "msg01"

    def test_system_session_id(self):
        assert self._make_info(SystemSessionId="xyz").system_session_id == "xyz"

    def test_is_low_speed_connection(self):
        assert self._make_info(IsLowSpeedConnection=True).is_low_speed_connection is True

    def test_scripting_mode_read_only(self):
        assert self._make_info(ScriptingModeReadOnly=True).scripting_mode_read_only is True

    def test_scripting_mode_recording_disabled(self):
        assert self._make_info(ScriptingModeRecordingDisabled=True).scripting_mode_recording_disabled is True

    def test_repr(self):
        info = self._make_info(SystemName="DEV", Client="100", User="TEST", Transaction="SE38")
        r = repr(info)
        assert "DEV" in r
        assert "100" in r
        assert "TEST" in r
        assert "SE38" in r


class TestGuiSessionRecording:
    """Issue #125: RecordFile/Record wrapper (filename rules verified live)."""

    def test_start_recording_sets_recordfile_then_record_and_returns_resolved_path(self):
        com = make_mock_com()
        events: list[tuple[str, object]] = []
        type(com).RecordFile = property(
            lambda _: r"C:\Scripts\JOURNEY3.VBS", lambda _, v: events.append(("RecordFile", v))
        )
        type(com).Record = property(lambda _: False, lambda _, v: events.append(("Record", v)))
        resolved = GuiSession(com).start_recording("journey3.vbs")
        assert events == [("RecordFile", "journey3.vbs"), ("Record", True)]
        assert resolved == r"C:\Scripts\JOURNEY3.VBS"

    @pytest.mark.parametrize("name", ["journey3.vbs", "abc", "1abc.vbs", "A.TXT"])
    def test_accepts_names_sap_gui_accepts(self, name):
        com = make_mock_com()
        GuiSession(com).start_recording(name)
        assert com.RecordFile == name

    @pytest.mark.parametrize(
        "name", ["my_journey.vbs", "my-name.vbs", "a b.vbs", "x.y.vbs", "", r"C:\x.vbs", "a/b.vbs"]
    )
    def test_rejects_names_sap_gui_rejects_before_touching_com(self, name):
        com = make_mock_com()
        com.Record = False
        com.RecordFile = "untouched"
        with pytest.raises(ValueError, match="invalid recording filename"):
            GuiSession(com).start_recording(name)
        assert com.Record is False
        assert com.RecordFile == "untouched"

    def test_stop_recording_clears_record(self):
        com = make_mock_com()
        com.Record = True
        GuiSession(com).stop_recording()
        assert com.Record is False

    def test_is_recording_reflects_com_state(self):
        com = make_mock_com()
        com.Record = False
        session = GuiSession(com)
        assert session.is_recording is False
        com.Record = True
        assert session.is_recording is True

    def test_com_error_propagates(self):
        com = make_mock_com()

        def refuse(_, value):
            raise RuntimeError("recording disabled")

        type(com).RecordFile = property(lambda _: "x", refuse)
        with pytest.raises(RuntimeError, match="recording disabled"):
            GuiSession(com).start_recording("journey.vbs")
