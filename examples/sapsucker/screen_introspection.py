"""Walk an entire SAP screen and print every element, then dump it as JSON.

Mirrors the "Read an entire screen" section of the README. If you change one,
change the other — ``unittests/test_screen_introspection_example.py`` asserts
that the README snippet and this file stay in sync.

Prerequisites:
    - SAP GUI for Windows must be running with at least one logged-in session.
    - SAP GUI Scripting must be enabled (transaction RZ11, parameter
      sapgui/user_scripting = TRUE).

Usage:
    python screen_introspection.py
"""

import json
from typing import Any

from sapsucker import SapGui
from sapsucker.models import ElementInfo


def walk(elements: list[ElementInfo], depth: int = 0) -> None:
    """Print an ElementInfo tree recursively; children nest inside each node."""
    for element in elements:
        print("  " * depth, element.id, element.type, element.text)
        walk(element.children, depth + 1)


def main(session: Any = None) -> None:
    """Run the example. Pass a session for testing, or None to auto-connect."""
    if session is None:
        app = SapGui.connect()
        session = app.connections[0].sessions[0]  # type: ignore[attr-defined]

    window = session.find_by_id("wnd[0]")

    # Full depth by default (safety cap: 200). Pass max_depth=2 to bound it.
    walk(window.dump_tree())

    # GetObjectTree returns a JSON *string*, one COM call, only the properties
    # you list. Unlike dump_tree() this has no fallback: it raises on SAP GUI
    # older than 7.70 PL3.
    raw = session.get_object_tree("wnd[0]", props=["Id", "Type", "Text"])
    tree = json.loads(raw)
    print(f"top-level element: {tree['children'][0]['properties']['Id']}")

    # props=None returns Id only — the cheapest possible screen dump.
    ids_only_json = session.get_object_tree("wnd[0]")
    print(f"ids-only dump is {len(ids_only_json)} characters of JSON")


if __name__ == "__main__":
    main()
