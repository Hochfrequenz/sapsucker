"""Tests for Pydantic data models."""

from sapsucker.models import ElementInfo, SessionInfo


class TestSessionInfo:
    def test_creation(self):
        info = SessionInfo(
            system_name="DEV",
            client="100",
            user="TESTUSER",
            language="EN",
            transaction="SE38",
            program="SAPMSSY0",
            screen_number=1000,
            application_server="appserver01",
            response_time=42,
            round_trips=3,
        )
        assert info.system_name == "DEV"
        assert info.client == "100"
        assert info.user == "TESTUSER"
        assert info.language == "EN"
        assert info.transaction == "SE38"
        assert info.program == "SAPMSSY0"
        assert info.screen_number == 1000
        assert info.application_server == "appserver01"
        assert info.response_time == 42
        assert info.round_trips == 3

    def test_serialization(self):
        info = SessionInfo(
            system_name="DEV",
            client="100",
            user="TESTUSER",
            language="EN",
            transaction="SE38",
            program="SAPMSSY0",
            screen_number=1000,
            application_server="appserver01",
            response_time=42,
            round_trips=3,
        )
        d = info.model_dump()
        assert d["system_name"] == "DEV"
        assert d["screen_number"] == 1000
        assert isinstance(d, dict)

        restored = SessionInfo.model_validate(d)
        assert restored == info


class TestElementInfo:
    def test_creation(self):
        elem = ElementInfo(
            id="/app/con[0]/ses[0]/wnd[0]/usr/txtFIELD",
            type="GuiTextField",
            type_as_number=31,
            name="txtFIELD",
            text="hello",
            changeable=True,
        )
        assert elem.id == "/app/con[0]/ses[0]/wnd[0]/usr/txtFIELD"
        assert elem.type == "GuiTextField"
        assert elem.type_as_number == 31
        assert elem.name == "txtFIELD"
        assert elem.text == "hello"
        assert elem.changeable is True

    def test_default_empty_children(self):
        elem = ElementInfo(
            id="x",
            type="GuiTextField",
            type_as_number=31,
            name="x",
            text="",
            changeable=False,
        )
        assert elem.children == []

    def test_nested_children(self):
        child = ElementInfo(
            id="child1",
            type="GuiTextField",
            type_as_number=31,
            name="txtC1",
            text="c1",
            changeable=True,
        )
        parent = ElementInfo(
            id="parent",
            type="GuiUserArea",
            type_as_number=74,
            name="usr",
            text="",
            changeable=False,
            children=[child],
        )
        assert len(parent.children) == 1
        assert parent.children[0].id == "child1"
        assert parent.children[0].name == "txtC1"

    def test_serialization_with_children(self):
        child = ElementInfo(
            id="child1",
            type="GuiTextField",
            type_as_number=31,
            name="txtC1",
            text="c1",
            changeable=True,
        )
        parent = ElementInfo(
            id="parent",
            type="GuiUserArea",
            type_as_number=74,
            name="usr",
            text="",
            changeable=False,
            children=[child],
        )
        d = parent.model_dump()
        assert len(d["children"]) == 1
        assert d["children"][0]["id"] == "child1"

        restored = ElementInfo.model_validate(d)
        assert restored == parent

    def test_new_fields_have_defaults(self):
        elem = ElementInfo(
            id="x",
            type="GuiTextField",
            type_as_number=31,
            name="x",
            text="",
            changeable=False,
        )
        assert elem.tooltip == ""
        assert elem.icon_name == ""
        assert elem.modified is False
        assert elem.height == 0
        assert elem.container_type is False

    def test_new_fields_populated(self):
        elem = ElementInfo(
            id="btn",
            type="GuiButton",
            type_as_number=40,
            name="btn[0]",
            text="Save",
            changeable=True,
            tooltip="Save Document (Ctrl+S)",
            icon_name="ICON_SAVE",
            height=25,
            container_type=False,
        )
        assert elem.tooltip == "Save Document (Ctrl+S)"
        assert elem.icon_name == "ICON_SAVE"
        assert elem.height == 25

    def test_serialization_includes_new_fields(self):
        elem = ElementInfo(
            id="x",
            type="GuiButton",
            type_as_number=40,
            name="x",
            text="",
            changeable=True,
            tooltip="Weiter (Enter)",
            icon_name="B_OKAY",
        )
        d = elem.model_dump()
        assert d["tooltip"] == "Weiter (Enter)"
        assert d["icon_name"] == "B_OKAY"
        restored = ElementInfo.model_validate(d)
        assert restored.tooltip == "Weiter (Enter)"
