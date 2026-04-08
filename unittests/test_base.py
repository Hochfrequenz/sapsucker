"""Tests for base component classes."""

import logging
from unittest.mock import MagicMock

import pytest

from sapsucker._errors import ElementNotFoundError
from sapsucker.components.base import (
    GuiComponent,
    GuiContainer,
    GuiVComponent,
    GuiVContainer,
    _dump_tree_recursive,
)
from sapsucker.models import ElementInfo
from unittests.conftest import make_mock_com

# ---------------------------------------------------------------------------
# GuiComponent
# ---------------------------------------------------------------------------


class TestGuiComponent:
    def test_com_property(self, mock_com):
        comp = GuiComponent(mock_com)
        assert comp.com is mock_com

    def test_id_property(self, mock_com):
        mock_com.Id = "/app/con[0]/ses[0]"
        comp = GuiComponent(mock_com)
        assert comp.id == "/app/con[0]/ses[0]"

    def test_name_property(self, mock_com):
        mock_com.Name = "btnOK"
        comp = GuiComponent(mock_com)
        assert comp.name == "btnOK"

    def test_type_property(self, mock_com):
        mock_com.Type = "GuiButton"
        comp = GuiComponent(mock_com)
        assert comp.type == "GuiButton"

    def test_type_as_number_property(self, mock_com):
        mock_com.TypeAsNumber = 40
        comp = GuiComponent(mock_com)
        assert comp.type_as_number == 40

    def test_container_type_property(self, mock_com):
        mock_com.ContainerType = True
        comp = GuiComponent(mock_com)
        assert comp.container_type is True

    def test_parent_property(self, mock_com):
        parent_mock = MagicMock()
        mock_com.Parent = parent_mock
        comp = GuiComponent(mock_com)
        assert comp.parent is parent_mock

    def test_repr(self, mock_com):
        mock_com.Type = "GuiTextField"
        mock_com.Id = "/app/con[0]/ses[0]/wnd[0]/usr/txtFIELD"
        comp = GuiComponent(mock_com)
        r = repr(comp)
        assert "GuiComponent" in r
        assert "GuiTextField" in r
        assert "txtFIELD" in r


# ---------------------------------------------------------------------------
# GuiVComponent
# ---------------------------------------------------------------------------


class TestGuiVComponent:
    def test_text_read(self):
        com = make_mock_com(text="hello")
        vc = GuiVComponent(com)
        assert vc.text == "hello"

    def test_text_write(self):
        com = make_mock_com(text="old")
        vc = GuiVComponent(com)
        vc.text = "new"
        assert com.Text == "new"

    def test_tooltip(self):
        com = make_mock_com(tooltip="tip")
        vc = GuiVComponent(com)
        assert vc.tooltip == "tip"

    def test_default_tooltip(self):
        com = make_mock_com(default_tooltip="def tip")
        vc = GuiVComponent(com)
        assert vc.default_tooltip == "def tip"

    def test_changeable(self):
        com = make_mock_com(changeable=False)
        vc = GuiVComponent(com)
        assert vc.changeable is False

    def test_modified(self):
        com = make_mock_com(modified=True)
        vc = GuiVComponent(com)
        assert vc.modified is True

    def test_dimensions(self):
        com = make_mock_com(height=50, width=200, left=10, top=20, screen_left=100, screen_top=200)
        vc = GuiVComponent(com)
        assert vc.height == 50
        assert vc.width == 200
        assert vc.left == 10
        assert vc.top == 20
        assert vc.screen_left == 100
        assert vc.screen_top == 200

    def test_icon_name(self):
        com = make_mock_com(icon_name="ICON_OK")
        vc = GuiVComponent(com)
        assert vc.icon_name == "ICON_OK"

    def test_is_symbol_font(self):
        com = make_mock_com(is_symbol_font=True)
        vc = GuiVComponent(com)
        assert vc.is_symbol_font is True

    def test_acc_text(self):
        com = make_mock_com(acc_text="accessible")
        vc = GuiVComponent(com)
        assert vc.acc_text == "accessible"

    def test_acc_tooltip(self):
        com = make_mock_com(acc_tooltip="acc tip")
        vc = GuiVComponent(com)
        assert vc.acc_tooltip == "acc tip"

    def test_acc_text_on_request(self):
        com = make_mock_com(acc_text_on_request="on req")
        vc = GuiVComponent(com)
        assert vc.acc_text_on_request == "on req"

    def test_set_focus(self):
        com = make_mock_com()
        vc = GuiVComponent(com)
        vc.set_focus()
        com.SetFocus.assert_called_once()

    def test_visualize(self):
        com = make_mock_com()
        vc = GuiVComponent(com)
        vc.visualize(True)
        com.Visualize.assert_called_once_with(True)

    def test_dump_state(self):
        com = make_mock_com()
        sentinel = MagicMock()
        com.DumpState.return_value = sentinel
        vc = GuiVComponent(com)
        result = vc.dump_state("inner")
        com.DumpState.assert_called_once_with("inner")
        assert result is sentinel


# ---------------------------------------------------------------------------
# GuiContainer
# ---------------------------------------------------------------------------


class TestGuiContainer:
    def test_children_property(self):
        child1 = make_mock_com(type_as_number=31, type_name="GuiTextField", name="child1")
        child2 = make_mock_com(type_as_number=40, type_name="GuiButton", name="child2")
        com = make_mock_com(container_type=True, children=[child1, child2])
        gc = GuiContainer(com)
        children = gc.children
        from sapsucker.components.collection import GuiComponentCollection

        assert isinstance(children, GuiComponentCollection)
        assert len(children) == 2

    def test_find_by_id_delegates(self):
        com = make_mock_com(container_type=True, children=[])
        found = make_mock_com(type_as_number=31, type_name="GuiTextField")
        com.FindById.return_value = found
        gc = GuiContainer(com)
        result = gc.find_by_id("usr/txtFIELD")
        com.FindById.assert_called_once_with("usr/txtFIELD", False)
        # find_by_id now wraps via factory; verify it wraps the correct COM object
        assert result.com is found

    def test_find_by_id_not_found_raises(self):
        com = make_mock_com(container_type=True, children=[])
        com.FindById.return_value = None
        gc = GuiContainer(com)
        with pytest.raises(ElementNotFoundError, match="usr/txtFIELD"):
            gc.find_by_id("usr/txtFIELD")

    def test_find_by_id_not_found_no_raise(self):
        com = make_mock_com(container_type=True, children=[])
        com.FindById.return_value = None
        gc = GuiContainer(com)
        result = gc.find_by_id("usr/txtFIELD", raise_error=False)
        assert result is None


# ---------------------------------------------------------------------------
# GuiVContainer
# ---------------------------------------------------------------------------


class TestGuiVContainer:
    def test_inherits_container_and_vcomponent(self):
        assert issubclass(GuiVContainer, GuiContainer)
        assert issubclass(GuiVContainer, GuiVComponent)

    def test_find_by_name(self):
        com = make_mock_com(container_type=True, children=[])
        found = make_mock_com(type_as_number=31, type_name="GuiTextField")
        com.FindByName.return_value = found
        vc = GuiVContainer(com)
        result = vc.find_by_name("FIELD", "GuiTextField")
        com.FindByName.assert_called_once_with("FIELD", "GuiTextField")
        assert result.com is found

    def test_find_by_name_returns_none(self):
        com = make_mock_com(container_type=True, children=[])
        com.FindByName.return_value = None
        vc = GuiVContainer(com)
        result = vc.find_by_name("FIELD", "GuiTextField")
        assert result is None

    def test_find_by_name_ex(self):
        com = make_mock_com(container_type=True, children=[])
        found = make_mock_com(type_as_number=31, type_name="GuiTextField")
        com.FindByNameEx.return_value = found
        vc = GuiVContainer(com)
        result = vc.find_by_name_ex("FIELD", 31)
        com.FindByNameEx.assert_called_once_with("FIELD", 31)
        assert result.com is found

    def test_find_by_name_ex_returns_none(self):
        com = make_mock_com(container_type=True, children=[])
        com.FindByNameEx.return_value = None
        vc = GuiVContainer(com)
        result = vc.find_by_name_ex("FIELD", 31)
        assert result is None

    def test_find_all_by_name(self):
        com = make_mock_com(container_type=True, children=[])
        col_com = MagicMock()
        col_com.Count = 0
        com.FindAllByName.return_value = col_com
        vc = GuiVContainer(com)
        result = vc.find_all_by_name("FIELD", "GuiTextField")
        com.FindAllByName.assert_called_once_with("FIELD", "GuiTextField")
        from sapsucker.components.collection import GuiComponentCollection

        assert isinstance(result, GuiComponentCollection)

    def test_find_all_by_name_ex(self):
        com = make_mock_com(container_type=True, children=[])
        col_com = MagicMock()
        col_com.Count = 0
        com.FindAllByNameEx.return_value = col_com
        vc = GuiVContainer(com)
        result = vc.find_all_by_name_ex("FIELD", 31)
        com.FindAllByNameEx.assert_called_once_with("FIELD", 31)
        from sapsucker.components.collection import GuiComponentCollection

        assert isinstance(result, GuiComponentCollection)


# ---------------------------------------------------------------------------
# dump_tree / _dump_tree_recursive
# ---------------------------------------------------------------------------


class TestDumpTree:
    def test_single_level(self):
        child1 = make_mock_com(
            id="c1",
            type_name="GuiTextField",
            type_as_number=31,
            name="txtA",
            text="hello",
            changeable=True,
            container_type=False,
        )
        child2 = make_mock_com(
            id="c2",
            type_name="GuiButton",
            type_as_number=40,
            name="btnOK",
            text="OK",
            changeable=True,
            container_type=False,
        )
        parent_com = make_mock_com(container_type=True, children=[child1, child2])
        vc = GuiVContainer(parent_com)
        result = vc.dump_tree()
        assert len(result) == 2
        assert isinstance(result[0], ElementInfo)
        assert result[0].id == "c1"
        assert result[0].name == "txtA"
        assert result[0].text == "hello"
        assert result[1].id == "c2"
        assert result[1].name == "btnOK"
        assert result[0].children == []
        assert result[1].children == []

    def test_respects_max_depth(self):
        grandchild = make_mock_com(
            id="gc1",
            type_name="GuiTextField",
            type_as_number=31,
            name="txtGC",
            text="deep",
            changeable=False,
            container_type=False,
        )
        child = make_mock_com(
            id="c1",
            type_name="GuiUserArea",
            type_as_number=74,
            name="usr",
            text="",
            changeable=False,
            container_type=True,
            children=[grandchild],
        )
        parent_com = make_mock_com(container_type=True, children=[child])
        vc = GuiVContainer(parent_com)
        # max_depth=1 means only depth 0 children, no recursion into containers
        result = vc.dump_tree(max_depth=1)
        assert len(result) == 1
        assert result[0].id == "c1"
        assert result[0].children == []

    def test_recurses_into_nested_containers(self):
        grandchild = make_mock_com(
            id="gc1",
            type_name="GuiTextField",
            type_as_number=31,
            name="txtGC",
            text="deep",
            changeable=False,
            container_type=False,
        )
        child = make_mock_com(
            id="c1",
            type_name="GuiUserArea",
            type_as_number=74,
            name="usr",
            text="",
            changeable=False,
            container_type=True,
            children=[grandchild],
        )
        parent_com = make_mock_com(container_type=True, children=[child])
        vc = GuiVContainer(parent_com)
        result = vc.dump_tree(max_depth=10)
        assert len(result) == 1
        assert len(result[0].children) == 1
        assert result[0].children[0].id == "gc1"
        assert result[0].children[0].text == "deep"

    def test_handles_exception_in_children(self):
        """If Children raises on a non-usr container, return empty list."""
        parent_com = make_mock_com(container_type=True, id="/app/con[0]/ses[0]/wnd[0]/tbar[0]")
        # Force Children to raise
        type(parent_com).Children = property(lambda self: (_ for _ in ()).throw(RuntimeError("no children")))
        result = _dump_tree_recursive(parent_com, 0, 10)
        assert result == []

    def test_captures_tooltip_and_icon(self):
        child = make_mock_com(
            type_as_number=40,
            type_name="GuiButton",
            id="btn",
            name="btn[0]",
            text="",
            tooltip="Weiter (Enter)",
            icon_name="B_OKAY",
            height=25,
            width=80,
            container_type=False,
        )
        parent = make_mock_com(container_type=True, children=[child])
        vc = GuiVContainer(parent)
        result = vc.dump_tree()
        assert result[0].tooltip == "Weiter (Enter)"
        assert result[0].icon_name == "B_OKAY"
        assert result[0].height == 25
        assert result[0].container_type is False

    def test_safe_fallback_on_missing_property(self):
        child = make_mock_com(
            type_as_number=31,
            type_name="GuiTextField",
            id="txt",
            name="txtF",
            text="hello",
            container_type=False,
        )
        del child.Tooltip
        parent = make_mock_com(container_type=True, children=[child])
        vc = GuiVContainer(parent)
        result = vc.dump_tree()
        assert result[0].tooltip == ""
        assert result[0].text == "hello"

    def test_unlimited_depth_default(self):
        gc = make_mock_com(type_as_number=31, id="gc", name="gc", container_type=False)
        child = make_mock_com(type_as_number=74, id="c", name="c", container_type=True, children=[gc])
        parent = make_mock_com(container_type=True, children=[child])
        vc = GuiVContainer(parent)
        result = vc.dump_tree()  # No max_depth — should go full depth
        assert len(result) == 1
        assert len(result[0].children) == 1
        assert result[0].children[0].id == "gc"

    def test_explicit_max_depth_still_works(self):
        gc = make_mock_com(type_as_number=31, id="gc", name="gc", container_type=False)
        child = make_mock_com(type_as_number=74, id="c", name="c", container_type=True, children=[gc])
        parent = make_mock_com(container_type=True, children=[child])
        vc = GuiVContainer(parent)
        result = vc.dump_tree(max_depth=1)
        assert len(result) == 1
        assert result[0].children == []


class TestDumpTreePerfLogging:
    """Verify GuiVContainer.dump_tree emits a per-call perf log line.

    Downstream consumers (e.g. sapwebgui.mcp) correlate this log with
    their own per-tool-call latency to find which tools are dominated
    by tree-dump cost. The fields must remain stable; this test pins them.
    """

    def test_dump_tree_logs_event_with_required_fields(self, caplog):
        child1 = make_mock_com(type_as_number=31, id="c1", name="txtA", container_type=False)
        child2 = make_mock_com(type_as_number=40, id="c2", name="btnOK", container_type=False)
        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]/usr",
            children=[child1, child2],
        )
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree()

        records = [r for r in caplog.records if r.message == "dump_tree"]
        assert len(records) == 1, f"Expected exactly one dump_tree log line, got {len(records)}"

        rec = records[0]
        # Required fields — pinned for downstream log-correlation tooling.
        # Direct attribute access (not getattr) is the readable form; the
        # field names are the contract that downstream consumers depend on.
        assert isinstance(rec.duration_ms, int)
        assert rec.duration_ms >= 0
        assert rec.elements == 2
        assert rec.depth_reached == 1
        assert rec.max_depth_param is None  # None == unlimited
        assert rec.container_id == "/app/con[0]/ses[0]/wnd[0]/usr"

    def test_dump_tree_log_counts_nested_elements(self, caplog):
        """elements field reports the TOTAL count, including grandchildren."""
        gc = make_mock_com(type_as_number=31, id="gc", name="gc", container_type=False)
        child = make_mock_com(type_as_number=74, id="c", name="c", container_type=True, children=[gc])
        parent = make_mock_com(container_type=True, id="root", children=[child])
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree()

        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.elements == 2  # child + grandchild
        assert rec.depth_reached == 2  # 2 levels deep

    def test_dump_tree_log_max_depth_param_passes_through(self, caplog):
        """When the caller passes max_depth, it shows up in the log line."""
        parent = make_mock_com(container_type=True, id="root", children=[])
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree(max_depth=5)

        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.max_depth_param == 5

    def test_dump_tree_log_handles_dead_proxy_for_container_id(self, caplog):
        """If the COM proxy goes stale and Id read raises, log <unknown>.

        The dump itself returned successfully (Children was OK); only the
        post-dump Id read for logging failed. The log line still emits with
        ``container_id="<unknown>"`` instead of breaking the call.
        """
        parent = make_mock_com(container_type=True, id="root", children=[])
        # Replace the Id property with one that raises after the dump completes.
        type(parent).Id = property(lambda self: (_ for _ in ()).throw(RuntimeError("dead proxy")))
        vc = GuiVContainer(parent)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            result = vc.dump_tree()  # Must NOT raise

        assert result == []
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.container_id == "<unknown>"
