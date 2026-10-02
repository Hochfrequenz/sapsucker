"""Tests for GuiStatusbar message fields — issue #90."""

from unittest.mock import MagicMock

from sapsucker.components.statusbar import GuiStatusbar
from unittests.conftest import make_mock_com


def _make_statusbar(**kwargs):
    com = make_mock_com(type_as_number=103, type_name="GuiStatusbar", **kwargs)
    return GuiStatusbar(com)


class TestGuiStatusbar:
    def test_message_type(self):
        assert _make_statusbar(MessageType="E").message_type == "E"

    def test_message_id(self):
        assert _make_statusbar(MessageId="XY").message_id == "XY"

    def test_message_number(self):
        assert _make_statusbar(MessageNumber="123").message_number == "123"

    def test_message_number_coerced_to_str(self):
        # SAP GUI returns a str; an int must still yield str.
        assert _make_statusbar(MessageNumber=123).message_number == "123"

    def test_message_as_popup(self):
        assert _make_statusbar(MessageAsPopup=True).message_as_popup is True
        assert _make_statusbar(MessageAsPopup=False).message_as_popup is False

    def test_message_has_long_text(self):
        assert _make_statusbar(MessageHasLongText=True).message_has_long_text is True
        assert _make_statusbar(MessageHasLongText=False).message_has_long_text is False

    def test_fields_are_independent(self):
        bar = _make_statusbar(MessageId="AA", MessageNumber="001", MessageType="W")
        assert (bar.message_id, bar.message_number, bar.message_type) == ("AA", "001", "W")

    def test_message_id_strips_com_padding(self):
        padded = "DS" + " " * 18
        assert _make_statusbar(MessageId=padded).message_id == "DS"

    def test_message_id_keeps_non_space_characters(self):
        assert _make_statusbar(MessageId="S#" + " " * 18).message_id == "S#"
        assert _make_statusbar(MessageId=" DS   " + " " * 13).message_id == " DS"

    def test_message_number_keeps_zero_padding(self):
        assert _make_statusbar(MessageNumber="017").message_number == "017"

    def test_message_parameter_is_called_with_index(self):
        params = {0: "ZZNOSUCHTX"}
        method = MagicMock(side_effect=lambda i: params.get(i, ""))
        bar = _make_statusbar(MessageParameter=method)
        assert bar.message_parameter(0) == "ZZNOSUCHTX"
        assert bar.message_parameter(1) == ""
        assert bar.message_parameter(9) == ""
        assert [c.args for c in method.call_args_list] == [(0,), (1,), (9,)]

    def test_empty_status_bar(self):
        bar = _make_statusbar(
            MessageType="",
            MessageId="",
            MessageNumber="",
            MessageParameter=MagicMock(return_value=""),
        )
        assert bar.message_type == ""
        assert bar.message_id == ""
        assert bar.message_number == ""
        assert bar.message_parameter(0) == ""
