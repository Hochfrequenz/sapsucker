"""Establish how ALV grid reads behave past the loaded scroll window.

Read-only. Answers the questions #91 rests on, which have so far only been
reasoned about:

1. Does ``GetCellValue`` for a row outside the loaded window return blank,
   raise, or just work? #91's whole premise is the first of those.
2. Is the row index ABSOLUTE or WINDOW-RELATIVE? After scrolling to row N,
   does index 0 return the first row of the table or the first row on screen?
   Every paging loop depends on the answer and nothing has established it.
3. Does ``VisibleRowCount`` exist and what does it report?
4. Does SAP clamp ``FirstVisibleRow`` near the end of the grid, so a loop must
   read back what it actually got rather than trusting what it asked for?
5. Is the last visible row fully loaded, or does stepping by VisibleRowCount
   skip or blank a row?

Usage — get to an ALV with several hundred rows first (e.g. SE16N, table
BUT000, F8), then:

    uv run python scripts/probe_alv_paging.py
    uv run python scripts/probe_alv_paging.py --grid "wnd[0]/shellcont/shell" --column BU_SORT1

Nothing is written to SAP. It only reads cells and moves the scroll position,
which it restores at the end.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

CANDIDATE_GRIDS = [
    "wnd[0]/shellcont/shell",
    "wnd[0]/usr/cntlRESULT_LIST/shellcont/shell",
    "wnd[0]/usr/cntlGRID1/shellcont/shell",
    "wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell",
]


def attach() -> Any:
    from sapsucker import SapGui  # noqa: PLC0415

    app = SapGui.connect()
    connections = app.connections
    if len(connections) == 0:
        sys.exit("SAP GUI has no open connection. Log in, then retry.")
    sessions = connections[0].sessions  # type: ignore[attr-defined]
    if len(sessions) == 0:
        sys.exit("The connection has no session.")
    return sessions[0]


def find_grid(session: Any, given: str | None) -> tuple[Any, str]:
    for element_id in [given] if given else CANDIDATE_GRIDS:
        element = session.find_by_id(element_id, raise_error=False)
        if element is not None and hasattr(element, "row_count"):
            return element, element_id
    sys.exit(
        "No ALV grid found. Navigate to one (SE16N -> a large table -> F8) and pass\n"
        "  --grid <id>  if it is not at one of the usual paths."
    )


def read(grid: Any, row: int, column: str) -> str:
    """Read one cell, reporting how it failed rather than dying."""
    try:
        return str(grid.get_cell_value(row, column))
    except Exception as exc:
        return f"<raised {type(exc).__name__}>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", default=None, help="element id of the ALV grid")
    parser.add_argument("--column", default=None, help="column to sample (default: first in column_order)")
    parser.add_argument("--max-rows", type=int, default=400, help="cap on the unpaged sweep")
    args = parser.parse_args()

    session = attach()
    grid, grid_id = find_grid(session, args.grid)
    print(f"grid: {grid_id}")

    row_count = int(grid.row_count)
    columns = list(grid.column_order)
    column = args.column or (columns[0] if columns else "")
    if not column:
        sys.exit("grid reports no columns")

    visible = "<unavailable>"
    try:
        visible = str(grid.com.VisibleRowCount)
    except Exception as exc:
        visible = f"<raised {type(exc).__name__}: {exc}>"

    print(f"row_count={row_count}  columns={len(columns)}  sampling column={column!r}")
    print(f"COM VisibleRowCount={visible}   (Q3)")
    if row_count < 50:
        print("\n!! row_count is small — this grid cannot demonstrate the paging problem.")
        print("   Navigate to a table with several hundred rows and re-run.")
        return 1

    original = int(grid.first_visible_row)
    print(f"first_visible_row on entry={original}")

    # --- Q1: unpaged sweep from the top, without ever scrolling
    print("\n--- Q1: unpaged read from the top, no scrolling ---")
    grid.first_visible_row = 0
    sweep_limit = min(row_count, args.max_rows)
    values = [read(grid, r, column) for r in range(sweep_limit)]
    blanks = [i for i, v in enumerate(values) if v == ""]
    raised = [i for i, v in enumerate(values) if v.startswith("<raised")]
    print(f"read rows 0..{sweep_limit - 1}")
    print(f"  non-empty: {sweep_limit - len(blanks) - len(raised)}")
    print(f"  empty:     {len(blanks)}  first at {blanks[0] if blanks else '-'}")
    print(f"  raised:    {len(raised)}  first at {raised[0] if raised else '-'}")
    if raised:
        print(f"  example error: {values[raised[0]]}")
    if blanks:
        run_start = blanks[0]
        print(f"  -> blanks begin at row {run_start}; rows 0..{run_start - 1} read fine")
    elif not raised:
        print("  -> NO truncation: every row read non-empty without scrolling.")
        print("     If this holds, #91's premise is wrong for this grid/release.")

    # --- Q2: absolute or window-relative indexing?
    print("\n--- Q2: is the row index absolute or window-relative? ---")
    row0_at_top = read(grid, 0, column)
    target = min(row_count - 1, 200)
    grid.first_visible_row = target
    landed = int(grid.first_visible_row)
    row0_after_scroll = read(grid, 0, column)
    row_landed = read(grid, landed, column)
    print(f"  asked first_visible_row={target}, SAP reports {landed}   (Q4: clamped={landed != target})")
    print(f"  cell(0)      before scrolling: {row0_at_top!r}")
    print(f"  cell(0)      after  scrolling: {row0_after_scroll!r}")
    print(f"  cell({landed}) after scrolling: {row_landed!r}")
    if row0_after_scroll == row0_at_top:
        print("  -> ABSOLUTE indexing: index 0 still means the table's first row.")
    elif row0_after_scroll == "":
        print("  -> index 0 went blank after scrolling: absolute indexing, and the")
        print("     top of the table has been unloaded. This is the paging problem.")
    else:
        print("  -> WINDOW-RELATIVE indexing: index 0 now means the first row on screen.")
        print("     A paging loop must not use absolute row numbers.")

    # --- Q5: is the last row of a window fully loaded?
    print("\n--- Q5: stepping by VisibleRowCount ---")
    try:
        step = int(grid.com.VisibleRowCount)
        grid.first_visible_row = 0
        start = int(grid.first_visible_row)
        edge = [(r, read(grid, r, column)) for r in range(max(0, step - 2), step + 3) if r < row_count]
        print(f"  window starts at {start}, VisibleRowCount={step}")
        for r, v in edge:
            print(f"    row {r:4d}: {'<empty>' if v == '' else v!r}")
        print(f"  -> if the empty ones start before row {step}, stepping by VisibleRowCount skips rows")
    except Exception as exc:
        print(f"  could not step: {type(exc).__name__}: {exc}")

    grid.first_visible_row = original
    print(f"\nrestored first_visible_row={original}")
    print("\nPaste this whole output; it settles #91's premise and the loop design.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
