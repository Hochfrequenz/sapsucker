# sapsucker

```
    ⊹
   /▲\      ╭─────────────────────────────────╮
  (●  >     │  tapping into SAP,              │
   ║║║  ━━━━│  one typed wrapper at a time 🌳 │
  / ║ \     ╰─────────────────────────────────╯
 ╱  ║  ╲
```

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python Version](https://img.shields.io/pypi/pyversions/sapsucker)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/sapsucker)](https://pypi.org/project/sapsucker/)
![Unittests](https://github.com/Hochfrequenz/sapsucker/actions/workflows/unittests.yml/badge.svg)
![Linting](https://github.com/Hochfrequenz/sapsucker/actions/workflows/pythonlint.yml/badge.svg)
![Formatting](https://github.com/Hochfrequenz/sapsucker/actions/workflows/formatting.yml/badge.svg)
![Coverage](https://github.com/Hochfrequenz/sapsucker/actions/workflows/coverage.yml/badge.svg)

Typed Python wrapper for the SAP GUI Scripting API.

**sapsucker** gives you typed, IDE-friendly access to SAP GUI for Windows.
Instead of working with raw COM objects and guessing method names, you get
Python classes with autocomplete, type hints, and docstrings for every
SAP GUI element.

## Quickstart

```python
from sapsucker import SapGui

# Connect to running SAP GUI
app = SapGui.connect()
session = app.connections[0].sessions[0]

# Read session info
print(session.info.system_name)   # → "S4H"
print(session.info.user)          # → "DEVELOPER"

# Navigate to a transaction
session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nSE16"
session.find_by_id("wnd[0]").send_v_key(0)  # Enter

# Read the status bar
print(session.find_by_id("wnd[0]/sbar").text)
```

## Why sapsucker?

- **40+ typed wrapper classes** — `GuiGridView.get_cell_value()`, `GuiTree.expand_node()`, not generic `element.read("cell", row, col)`
- **IDE autocomplete & type hints** on every method and property
- **430+ unit tests**, 50+ integration tests verified against real SAP S/4 HANA
- **API verified** against the SAP GUI Scripting API 6.40 PDF (2969 pages)
- **MIT licensed** — no GPL restrictions

## Installation

```bash
pip install sapsucker
```

### Prerequisites

- **SAP GUI for Windows** (7.x or 8.x)
- **SAP GUI Scripting enabled** — ask your SAP Basis team to set `sapgui/user_scripting = TRUE` in transaction RZ11, and enable scripting in your SAP GUI options (Customize Local Layout → Accessibility & Scripting)
- **Python 3.11+** on Windows

## Usage Examples

### Read an ALV grid

```python
from sapsucker import SapGui
from sapsucker.components.grid import GuiGridView

app = SapGui.connect()
session = app.connections[0].sessions[0]

# Find the grid on the current screen
grid = session.find_by_id("wnd[0]/shellcont/shell")

# Read all rows
for row in range(grid.row_count):
    for col in grid.column_order:
        print(grid.get_cell_value(row, col), end="\t")
    print()
```

### Navigate a tree

```python
from sapsucker.components.tree import GuiTree

tree = session.find_by_id("wnd[0]/shellcont/shell/shellcont[1]/shell/shellcont[2]/shell")

key = tree.top_node
print(tree.get_node_text_by_key(key))

if tree.is_folder(key):
    tree.expand_node(key)
```

### Fill a form

```python
# Set a text field value
session.find_by_id("wnd[0]/usr/ctxtRS38M-PROGRAMM").text = "RSPARAM"

# Press F8 (Execute)
session.find_by_id("wnd[0]").send_v_key(8)
```

### Context manager

```python
with SapGui.connect() as app:
    session = app.connections[0].sessions[0]
    print(session.info.user)
# All connections closed automatically
```

### More examples

The [`examples/sapsucker/`](examples/sapsucker) directory contains complete runnable scripts, all tested against a real SAP system:

- [`basic_navigation.py`](examples/sapsucker/basic_navigation.py) — connect, read session info, navigate transactions
- [`alv_grid_export.py`](examples/sapsucker/alv_grid_export.py) — query SE16N and read ALV grid data
- [`form_filling.py`](examples/sapsucker/form_filling.py) — fill selection screens and execute reports
- [`tree_navigation.py`](examples/sapsucker/tree_navigation.py) — browse and expand tree controls in SE80

## Architecture

sapsucker wraps the SAP GUI Scripting COM API as a hierarchy of typed Python classes:

```
GuiApplication
  └── GuiConnection
       └── GuiSession
            └── GuiMainWindow
                 ├── GuiToolbar
                 ├── GuiMenubar
                 ├── GuiStatusbar
                 └── GuiUserArea
                      ├── GuiTextField, GuiLabel, GuiButton, ...
                      ├── GuiTableControl (classic dynpro tables)
                      ├── GuiGridView (ALV grids)
                      ├── GuiTree (tree controls)
                      ├── GuiTabStrip → GuiTab
                      └── GuiAbapEditor / GuiTextedit
```

Elements are discovered via `session.find_by_id(sap_id)`, which returns the
correct typed wrapper automatically (e.g., `GuiGridView` for an ALV grid,
`GuiTree` for a tree control). The factory dispatches on `TypeAsNumber` and
`SubType` COM properties.

## Thread Safety

COM objects use the Single-Threaded Apartment (STA) model. All calls to a
given SAP GUI session must happen from the same thread that called
`pythoncom.CoInitialize()`. See the `_com.py` module docstring for details
and an `asyncio.to_thread()` example.

## API Overview

| Class             | Description                                               |
| ----------------- | --------------------------------------------------------- |
| `SapGui`          | Entry point — `SapGui.connect()` returns `GuiApplication` |
| `GuiApplication`  | Root object, manages connections                          |
| `GuiConnection`   | A TCP connection to an SAP server                         |
| `GuiSession`      | A session (mode) within a connection                      |
| `GuiMainWindow`   | The main SAP window                                       |
| `GuiTextField`    | Single-line input field                                   |
| `GuiButton`       | Push button                                               |
| `GuiCheckBox`     | Checkbox                                                  |
| `GuiComboBox`     | Dropdown list                                             |
| `GuiGridView`     | ALV grid (most common data display)                       |
| `GuiTableControl` | Classic dynpro table                                      |
| `GuiTree`         | Tree control (simple, list, or column)                    |
| `GuiAbapEditor`   | ABAP source code editor                                   |
| `GuiStatusbar`    | Status bar at bottom of window                            |

## Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change.

For detailed setup instructions (tox environments, CI, linting, formatting, etc.), see the [Hochfrequenz Python Template Repository](https://github.com/Hochfrequenz/python_template_repository).

```bash
# Clone and install dev dependencies
git clone https://github.com/Hochfrequenz/sapsucker.git
cd sapsucker
pip install -e ".[dev]"

# Run unit tests (no SAP required, works on any OS)
pytest unittests/ -v
```

### Integration tests against real SAP

Integration tests run against a real SAP GUI system and are automatically skipped on machines without SAP access. To run them locally:

1. **SAP GUI for Windows** must be running with scripting enabled
2. Create a `.env` file with your SAP credentials:
   ```
   SAP_CONNECTION_NAME=your_connection
   SAP_USER=your_user
   SAP_PASSWORD=your_password
   SAP_MANDANT=your_client
   SAP_LANGUAGE=EN
   ```
3. Add your machine's hostname to `_AUTHORIZED_SAP_TEST_MACHINES` in `unittests/conftest.py`
4. Run:
   ```bash
   pytest unittests/ -k integration -v
   ```

Integration tests cover SE80 (tree), SE16N (grid), SE37 (table/tab), SE38 (editor), and SM37 (fields/window). They are read-only and do not modify SAP data.

## License

MIT
