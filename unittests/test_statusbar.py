"""Tests for GuiStatusbar message fields — issue #90."""

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
        # Return type of MessageNumber is not verified live; an int must still yield str.
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
