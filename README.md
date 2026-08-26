# 🐦 sapsucker

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python Version](https://img.shields.io/pypi/pyversions/sapsucker)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/sapsucker)](https://pypi.org/project/sapsucker/)
![Unittests](https://github.com/Hochfrequenz/sapsucker/actions/workflows/unittests.yml/badge.svg)
![Linting](https://github.com/Hochfrequenz/sapsucker/actions/workflows/pythonlint.yml/badge.svg)
![Formatting](https://github.com/Hochfrequenz/sapsucker/actions/workflows/formatting.yml/badge.svg)
![Coverage](https://github.com/Hochfrequenz/sapsucker/actions/workflows/coverage.yml/badge.svg)

Typed Python wrapper for the SAP GUI Scripting API.
For a Go client around the SAP ADT API, check [**adtler**](https://github.com/Hochfrequenz/adtler).

**sapsucker** gives you typed, IDE-friendly access to SAP GUI for Windows.
Instead of working with raw COM objects and guessing method names, you get
Python classes with autocomplete, type hints, and docstrings for every
SAP GUI element.

Named after the [sapsucker](https://en.wikipedia.org/wiki/Sapsucker) — a woodpecker that taps into trees to drink their sap. This library taps into SAP GUI to extract your data.

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

- **Read a whole screen in one call** — `container.dump_tree()` walks any screen recursively and returns typed `ElementInfo`, so you can discover element IDs instead of guessing them (the whole subtree in a single COM round trip on SAP GUI >= 7.70 PL3)
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

### Read an entire screen

Don't know a screen's element IDs? Walk it. `dump_tree()` recurses through every
child container and returns validated `ElementInfo` objects — id, type, name,
text, tooltips, accessibility text, geometry and more (`sapsucker.models.ElementInfo`).

```python
from sapsucker import SapGui
from sapsucker.models import ElementInfo

app = SapGui.connect()
session = app.connections[0].sessions[0]

window = session.find_by_id("wnd[0]")

def walk(elements: list[ElementInfo], depth: int = 0) -> None:
    for element in elements:
        print("  " * depth, element.id, element.type, element.text)
        walk(element.children, depth + 1)   # ElementInfo nests its children

walk(window.dump_tree())                    # full depth by default (safety cap: 200)
# walk(window.dump_tree(max_depth=2))       # or bound it
```

On SAP GUI for Windows >= 7.70 PL3 `dump_tree()` uses `GuiSession.GetObjectTree`
under the hood — the whole subtree in a single COM round trip — and falls back
automatically to per-property reads on older releases.

Feeding a screen to an LLM? Call `session.get_object_tree()` directly and ask for only the
properties you need instead of the full element record. Unlike `dump_tree()`,
this call has no fallback — it raises on SAP GUI older than 7.70 PL3.

```python
import json

# A JSON *string*, one COM call, only the properties you list
raw = session.get_object_tree("wnd[0]", props=["Id", "Type", "Text"])
tree = json.loads(raw)          # the queried element is tree["children"][0]

# props=None returns Id only — the cheapest possible screen dump
ids_only_json = session.get_object_tree("wnd[0]")
```

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

### Navigate a tree control

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
- [`screen_introspection.py`](examples/sapsucker/screen_introspection.py) — walk a whole screen with `dump_tree()` and dump it as JSON

## Monitoring a session while a human records

`sapsucker.monitor` samples a live session so a recording made with SAP GUI's own
recorder (`Alt+F12` → *Script Recording and Playback*) can be paired with
timestamps. A recorded `.vbs` has none, so it cannot tell you where the person
paused — which is usually where they were deciding — nor that they went back to
re-check a field, nor whether the save actually worked.

```bash
pip install sapsucker[cli]

sapsucker-monitor -o timing.jsonl \
  --watch "wnd[0]/shellcont/shell:FirstVisibleRow"
```

Start it, start the recorder, do the task, stop both. `--watch` takes any
`element_id:ComProperty` pair and is repeatable — the example above timestamps
each individual ALV scroll.

A prebuilt Windows `.exe` is attached to each [release](https://github.com/Hochfrequenz/sapsucker/releases)
for machines without a Python toolchain.

As a library:

```python
from sapsucker.monitor import SessionMonitor, Watch

monitor = SessionMonitor(session, watches=[Watch(element_id="wnd[0]/shellcont/shell", prop="FirstVisibleRow")])
for sample in monitor.samples():      # generator: the caller owns the loop, and the thread
    if sample.changed:
        print(sample.elapsed_s, sample.changed, sample.gap_since_change_s)
```

`samples()` never starts a thread. COM is STA, so a monitor loop occupies its
thread for its whole lifetime — see the `sapsucker.monitor` module docstring.

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

| Class / method    | Description                                               |
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
| `.dump_tree()`    | Method on any visual container (`GuiVContainer`) — recursive screen dump, returns `list[ElementInfo]` |

## Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change.

For detailed setup instructions (uv dependency groups, CI, linting, formatting, etc.), see the [Hochfrequenz Python Template Repository](https://github.com/Hochfrequenz/python_template_repository).

```bash
# Clone and install dev dependencies
git clone https://github.com/Hochfrequenz/sapsucker.git
cd sapsucker
uv sync --group dev

# Run unit tests (no SAP required, works on any OS)
uv run pytest unittests/ -v
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
3. Run:
   ```bash
   uv run pytest unittests/ -k integration -v
   ```

Integration tests run by default on any local machine and are automatically skipped in CI (GitHub Actions). Set `SAP_SKIP_INTEGRATION=1` to skip them locally. They cover SE80, SE16N, SE37, SE38, and SM37 — all read-only, no SAP data is modified.

## License

MIT
