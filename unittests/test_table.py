"""Tests for GuiTableControl, GuiTableRow, GuiTableColumn — issues #476, #517."""

from unittest.mock import MagicMock, patch

from sapsucker.components.base import GuiComponent
from sapsucker.components.table import (
    GuiTableColumn,
    GuiTableControl,
    GuiTableRow,
)


def _make_table(**kwargs):
    com = MagicMock()
    com.TypeAsNumber = 80
    for k, v in kwargs.items():
        setattr(com, k, v)
    return GuiTableControl(com)


def _make_row(**kwargs):
    com = MagicMock()
    for k, v in kwargs.items():
        setattr(com, k, v)
    return GuiTableRow(com)


def _make_column(**kwargs):
    com = MagicMock()
    for k, v in kwargs.items():
        setattr(com, k, v)
    return GuiTableColumn(com)


class TestGuiTableControl:
    def test_row_count(self):
        tbl = _make_table(RowCount=10)
        assert tbl.row_count == 10

    def test_visible_row_count(self):
        tbl = _make_table(VisibleRowCount=5)
        assert tbl.visible_row_count == 5

    def test_current_row_get(self):
        tbl = _make_table(CurrentRow=3)
        assert tbl.current_row == 3

    def test_current_row_set(self):
        tbl = _make_table()
        tbl.current_row = 7
        assert tbl._com.CurrentRow == 7

    def test_current_col_get(self):
        tbl = _make_table(CurrentCol=2)
        assert tbl.current_col == 2

    def test_current_col_set(self):
        tbl = _make_table()
        tbl.current_col = 4
        assert tbl._com.CurrentCol == 4

    def test_columns(self):
        tbl = _make_table()
        tbl._com.Columns.Count = 3
        assert tbl.columns.Count == 3

    def test_rows(self):
        tbl = _make_table()
        tbl._com.Rows.Count = 5
        assert tbl.rows.Count == 5

    def test_get_cell_calls_wrap(self):
        tbl = _make_table()
        cell_com = MagicMock()
        tbl._com.GetCell.return_value = cell_com
        wrapped = MagicMock(spec=GuiComponent)
        with patch("sapsucker.components.table.wrap_com_object", return_value=wrapped) as mock_wrap:
            result = tbl.get_cell(0, 1)
        tbl._com.GetCell.assert_called_once_with(0, 1)
        mock_wrap.assert_called_once_with(cell_com)
        assert result is wrapped

    def test_get_cell_returns_gui_component(self):
        """get_cell returns a GuiComponent, not a raw COM object."""
        from unittests.conftest import make_mock_com
        tbl = _make_table()
        cell_com = make_mock_com(type_as_number=31)  # GuiTextField
        tbl._com.GetCell.return_value = cell_com
        result = tbl.get_cell(0, 0)
        assert isinstance(result, GuiComponent)

    def test_get_absolute_row(self):
        tbl = _make_table()
        row_com = MagicMock()
        tbl._com.GetAbsoluteRow.return_value = row_com
        result = tbl.get_absolute_row(5)
        tbl._com.GetAbsoluteRow.assert_called_once_with(5)
        assert isinstance(result, GuiTableRow)


class TestGuiTableRow:
    def test_selected_getter_true(self):
        row = _make_row(Selected=1)
        assert row.selected is True

    def test_selected_getter_false(self):
        row = _make_row(Selected=0)
        assert row.selected is False

    def test_selected_setter_true(self):
        row = _make_row()
        row.selected = True
        assert row._com.Selected == 1

    def test_selected_setter_false(self):
        row = _make_row()
        row.selected = False
        assert row._com.Selected == 0

    def test_selectable(self):
        row = _make_row(Selectable=True)
        assert row.selectable is True


class TestGuiTableColumn:
    def test_title(self):
        col = _make_column(Title="Material")
        assert col.title == "Material"

    def test_selected_getter(self):
        col = _make_column(Selected=1)
        assert col.selected is True

    def test_selected_setter(self):
        col = _make_column()
        col.selected = True
        assert col._com.Selected == 1

    def test_selected_setter_false(self):
        col = _make_column()
        col.selected = False
        assert col._com.Selected == 0
