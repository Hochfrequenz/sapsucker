"""Tests for GuiGridView missing methods — issue #473."""

from unittest.mock import MagicMock

import pytest

from sapsucker.components.grid import GuiGridView


def _make_grid():
    com = MagicMock()
    com.TypeAsNumber = 122
    com.SubType = "GridView"
    return GuiGridView(com)


class TestGuiGridViewCellInfo:
    def test_get_cell_color(self):
        grid = _make_grid()
        grid._com.GetCellColor.return_value = 3
        assert grid.get_cell_color(0, "COL") == 3
        grid._com.GetCellColor.assert_called_once_with(0, "COL")

    def test_get_cell_icon(self):
        grid = _make_grid()
        grid._com.GetCellIcon.return_value = "@01@"
        assert grid.get_cell_icon(0, "COL") == "@01@"

    def test_get_cell_state(self):
        grid = _make_grid()
        grid._com.GetCellState.return_value = "Normal"
        assert grid.get_cell_state(0, "COL") == "Normal"

    def test_modify_cell(self):
        grid = _make_grid()
        grid.modify_cell(0, "COL", "new")
        grid._com.ModifyCell.assert_called_once_with(0, "COL", "new")

    def test_is_cell_hotspot(self):
        grid = _make_grid()
        grid._com.IsCellHotspot.return_value = True
        assert grid.is_cell_hotspot(0, "COL") is True

    def test_get_cell_tooltip(self):
        grid = _make_grid()
        grid._com.GetCellTooltip.return_value = "hint"
        assert grid.get_cell_tooltip(0, "COL") == "hint"


class TestGuiGridViewColumnInfo:
    def test_get_displayed_column_title(self):
        grid = _make_grid()
        grid._com.GetDisplayedColumnTitle.return_value = "Material"
        assert grid.get_displayed_column_title("MATNR") == "Material"

    def test_get_column_tooltip(self):
        grid = _make_grid()
        grid._com.GetColumnTooltip.return_value = "tip"
        assert grid.get_column_tooltip("COL") == "tip"

    def test_get_column_data_type(self):
        grid = _make_grid()
        grid._com.GetColumnDataType.return_value = "CHAR"
        assert grid.get_column_data_type("COL") == "CHAR"


class TestGuiGridViewPress:
    def test_press_f4(self):
        """Issue #92: value help on the current ALV cell maps to PressF4."""
        grid = _make_grid()
        assert hasattr(grid, "press_f4"), "GuiGridView.press_f4 is missing"
        grid.press_f4()
        grid._com.PressF4.assert_called_once()


class TestGuiGridViewToDicts:
    """Issue #91: to_dicts pages through the loaded window instead of reading a prefix."""

    def _make_paged_grid(self, total: int, page_size: int):
        """COM stub where only rows inside [FirstVisibleRow, +page_size) have values."""
        com = MagicMock()
        com.TypeAsNumber = 122
        com.SubType = "GridView"
        com.RowCount = total
        com.ColumnOrder = MagicMock()
        com.ColumnOrder.Count = 2
        com.ColumnOrder.side_effect = lambda i: ["MANDT", "MWAER"][i]
        com.FirstVisibleRow = 0

        def get_cell_value(row, col):
            first = com.FirstVisibleRow
            if first <= row < first + page_size:
                return f"v{row}"
            return ""  # outside the loaded window: blank

        com.GetCellValue.side_effect = get_cell_value
        return GuiGridView(com), com

    def test_reads_all_rows_paging(self):
        grid, com = self._make_paged_grid(total=250, page_size=100)
        rows = grid.to_dicts()
        assert len(rows) == 250
        # Every row must have a value — a blank row means we read outside the window.
        assert all(r["MANDT"] == f"v{i}" for i, r in enumerate(rows)), "row read outside the loaded window"
        # FirstVisibleRow was advanced and reset.
        assert com.FirstVisibleRow == 0

    def test_partial_final_page(self):
        grid, _com = self._make_paged_grid(total=101, page_size=100)
        rows = grid.to_dicts()
        assert len(rows) == 101
        assert rows[100]["MWAER"] == "v100"

    def test_on_page_callback(self):
        grid, _com = self._make_paged_grid(total=205, page_size=100)
        pages: list[tuple[int, int]] = []
        grid.to_dicts(on_page=lambda a, b: pages.append((a, b)))
        assert pages == [(0, 100), (100, 200), (200, 205)]

    def test_non_positive_page_size_rejected(self):
        """Copilot review of #120: page_size <= 0 would never advance the window."""
        grid, _com = self._make_paged_grid(total=10, page_size=5)
        with pytest.raises(ValueError, match="page_size"):
            grid.to_dicts(page_size=0)
        with pytest.raises(ValueError, match="page_size"):
            grid.to_dicts(page_size=-5)
