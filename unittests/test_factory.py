"""Tests for the two-level type dispatch factory."""

from __future__ import annotations

import pytest

from sapsucker._factory import (
    _SHELL_SUBTYPE_MAP,
    _TYPE_MAP,
    wrap_com_object,
)
from sapsucker._wrap import com_collection_item
from sapsucker.components.base import GuiComponent
from sapsucker.components.button import GuiButton
from sapsucker.components.field import GuiTextField
from sapsucker.components.grid import GuiGridView
from sapsucker.components.shell import GuiShell
from sapsucker.components.tree import GuiTree
from sapsucker.components.window import GuiMainWindow
from unittests.conftest import make_mock_com


class TestWrapComObject:
    """Test wrap_com_object dispatches to the correct class."""

    def test_main_window(self):
        com = make_mock_com(type_as_number=21, type_name="GuiMainWindow")
        result = wrap_com_object(com)
        assert isinstance(result, GuiMainWindow)

    def test_text_field(self):
        com = make_mock_com(type_as_number=31, type_name="GuiTextField")
        result = wrap_com_object(com)
        assert isinstance(result, GuiTextField)

    def test_button(self):
        com = make_mock_com(type_as_number=40, type_name="GuiButton")
        result = wrap_com_object(com)
        assert isinstance(result, GuiButton)

    def test_unknown_type_falls_back_to_gui_component(self):
        com = make_mock_com(type_as_number=9999, type_name="GuiUnknown")
        result = wrap_com_object(com)
        assert type(result) is GuiComponent

    def test_shell_gridview_dispatch(self):
        com = make_mock_com(type_as_number=122, type_name="GuiShell", SubType="GridView")
        result = wrap_com_object(com)
        assert isinstance(result, GuiGridView)

    def test_shell_tree_dispatch(self):
        com = make_mock_com(type_as_number=122, type_name="GuiShell", SubType="Tree")
        result = wrap_com_object(com)
        assert isinstance(result, GuiTree)

    def test_shell_unknown_subtype_falls_back_to_gui_shell(self):
        com = make_mock_com(type_as_number=122, type_name="GuiShell", SubType="UnknownControl")
        result = wrap_com_object(com)
        assert type(result) is GuiShell

    def test_shell_missing_subtype_falls_back_to_gui_shell(self):
        com = make_mock_com(type_as_number=122, type_name="GuiShell")
        # MagicMock will auto-create SubType, so delete it to test getattr fallback
        del com.SubType
        result = wrap_com_object(com)
        assert type(result) is GuiShell


class TestTypeMapCompleteness:
    """Verify all _TYPE_MAP entries produce valid instances."""

    @pytest.mark.parametrize("type_num,cls", list(_TYPE_MAP.items()), ids=[str(k) for k in _TYPE_MAP])
    def test_type_map_entry(self, type_num, cls):
        com = make_mock_com(type_as_number=type_num, type_name=cls.__name__)
        # For shell types (122), add SubType so it doesn't accidentally sub-dispatch
        if type_num != 122:
            result = wrap_com_object(com)
            assert isinstance(result, cls)

    def test_shell_base_with_no_matching_subtype(self):
        com = make_mock_com(type_as_number=122, type_name="GuiShell", SubType="__nonexistent__")
        result = wrap_com_object(com)
        assert type(result) is GuiShell


class TestShellSubtypeMapCompleteness:
    """Verify all _SHELL_SUBTYPE_MAP entries produce valid instances."""

    @pytest.mark.parametrize(
        "sub_type,cls",
        list(_SHELL_SUBTYPE_MAP.items()),
        ids=list(_SHELL_SUBTYPE_MAP.keys()),
    )
    def test_shell_subtype_entry(self, sub_type, cls):
        com = make_mock_com(type_as_number=122, type_name="GuiShell", SubType=sub_type)
        result = wrap_com_object(com)
        assert isinstance(result, cls)


class _FakeComError(Exception):
    """Stand-in for pywintypes.com_error carrying SAP GUI error 618.

    Mirrors the real payload observed on the failing host (sapgui.mcp#804):
    ``(-2147352567, 'Exception occurred.', (618, 'saplogon',
    'Bad index type for collection access.', None, 0, 0), None)``.
    """


_BAD_INDEX_ARGS = (
    -2147352567,
    "Exception occurred.",
    (618, "saplogon", "Bad index type for collection access.", None, 0, 0),
    None,
)


class _FakeCollection:
    """Fake SAP GUI COM collection with independently-toggleable accessors.

    Models the exact quirk from sapgui.mcp#804: ``Item(<int>)`` can raise COM
    error 618 while ``ElementAt(<int>)`` and the default member ``collection(<int>)``
    return the element fine.
    """

    def __init__(self, items, *, item_ok=True, element_at_ok=True, default_ok=True, has_element_at=True):
        self._items = items
        self._item_ok = item_ok
        self._element_at_ok = element_at_ok
        self._default_ok = default_ok
        self._has_element_at = has_element_at
        self.Count = len(items)

    def Item(self, index):
        if not self._item_ok:
            raise _FakeComError(*_BAD_INDEX_ARGS)
        return self._items[index]

    def __call__(self, index):  # default member: collection(index)
        if not self._default_ok:
            raise _FakeComError(*_BAD_INDEX_ARGS)
        return self._items[index]

    def __getattr__(self, name):
        # Only synthesise ElementAt; everything else is a genuine miss so that
        # ``getattr(col, "ElementAt", None)`` returns None when absent.
        if name == "ElementAt" and self.__dict__.get("_has_element_at", False):

            def _element_at(index):
                if not self._element_at_ok:
                    raise _FakeComError(*_BAD_INDEX_ARGS)
                return self._items[index]

            return _element_at
        raise AttributeError(name)


class TestComCollectionItem:
    """Tests for com_collection_item() — the SAP GUI error 618 workaround (mcp#804)."""

    def test_uses_item_when_it_works(self):
        """When Item(index) succeeds it is used and ElementAt is never consulted."""
        items = ["a", "b", "c"]
        # ElementAt would raise if reached — proves it is not reached.
        col = _FakeCollection(items, item_ok=True, element_at_ok=False)
        assert com_collection_item(col, 1) == "b"

    def test_falls_back_to_element_at_on_bad_index(self):
        """Item raising 618 falls back to ElementAt(index)."""
        items = ["a", "b", "c"]
        col = _FakeCollection(items, item_ok=False, element_at_ok=True)
        assert com_collection_item(col, 2) == "c"

    def test_falls_back_to_default_member_when_item_and_element_at_fail(self):
        """Item and ElementAt both raising falls back to the default member call."""
        items = ["a", "b", "c"]
        col = _FakeCollection(items, item_ok=False, element_at_ok=False, default_ok=True)
        assert com_collection_item(col, 0) == "a"

    def test_falls_back_to_default_member_when_element_at_absent(self):
        """A collection without ElementAt falls straight to the default member."""
        items = ["a", "b"]
        col = _FakeCollection(items, item_ok=False, has_element_at=False, default_ok=True)
        assert com_collection_item(col, 1) == "b"

    def test_reraises_original_item_error_when_all_strategies_fail(self):
        """If every access strategy fails, the ORIGINAL Item error is re-raised."""
        col = _FakeCollection(["a"], item_ok=False, element_at_ok=False, default_ok=False)
        with pytest.raises(_FakeComError) as exc_info:
            com_collection_item(col, 0)
        # The re-raised error is the one from Item (carries the 618 payload), not a
        # masked/replaced exception from a later fallback.
        assert exc_info.value.args[2][0] == 618
