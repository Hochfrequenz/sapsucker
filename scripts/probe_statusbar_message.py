"""Live check of the GuiStatusbar message fields (issue #90).

Needs a running SAP GUI with scripting enabled and at least one open session.
Two cases are run and every wrapped status-bar message member is printed:

1. A non-existent transaction code via /n: expected message_type "S", message_id
   "S#", message_number "343", message_parameter(0) == the transaction code.
2. SE38 display of a non-existent program: expected message_type "E", message_id
   "DS", message_number "017", message_parameter(0) == the program name.

    uv run python scripts/probe_statusbar_message.py

Exit code 0 only if both cases match those expectations. Anything else (including
an exception reading a member) exits 1. Paste the full output back.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sapsucker import SapGui  # noqa: E402

TX = "ZZNOSUCHTX"
PROG = "ZZNOSUCHPROG"


def _read(bar: object, label: str) -> bool:
    """Print every wrapped member; return True if reading any of them raised."""
    raised = False
    print(f"--- {label} ---")
    print(f"text: {bar.text!r}")  # type: ignore[attr-defined]
    for name in ("message_type", "message_id", "message_number", "message_as_popup", "message_has_long_text"):
        try:
            value = getattr(bar, name)
            print(f"{name}: {value!r} ({type(value).__name__})")
        except Exception as exc:  # noqa: BLE001 - a probe must report, not crash
            raised = True
            print(f"{name}: RAISED {type(exc).__name__}: {exc}")
    for i in range(4):
        try:
            print(f"message_parameter({i}): {bar.message_parameter(i)!r}")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            raised = True
            print(f"message_parameter({i}): RAISED {type(exc).__name__}: {exc}")
    return raised


def _check(bar: object, label: str, mtype: str, mid: str, mnum: str, param0: str) -> bool:
    """Return True if the case failed."""
    failed = _read(bar, label)
    try:
        actual = (
            bar.message_type,  # type: ignore[attr-defined]
            bar.message_id,  # type: ignore[attr-defined]
            bar.message_number,  # type: ignore[attr-defined]
            bar.message_parameter(0),  # type: ignore[attr-defined]
        )
    except Exception:  # noqa: BLE001
        return True
    expected = (mtype, mid, mnum, param0)
    if actual != expected:
        print(f"FAIL ({label}): expected {expected!r}, got {actual!r}")
        failed = True
    return failed


def main() -> int:
    app = SapGui.connect()
    try:
        session = app.active_session
    except AttributeError:  # no SAP GUI window has focus; fall back to the first session
        session = app.connections[0].sessions[0]

    failed = False

    # Case 1: nonexistent transaction -> "S" message
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = f"/n{TX}"
    session.find_by_id("wnd[0]").send_v_key(0)
    bar = session.find_by_id("wnd[0]/sbar")
    failed |= _check(bar, "nonexistent transaction", "S", "S#", "343", TX)

    # Case 2: SE38 display of a nonexistent program -> "E" message
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nSE38"
    session.find_by_id("wnd[0]").send_v_key(0)
    session.find_by_id("wnd[0]/usr/ctxtRS38M-PROGRAMM").text = PROG
    session.find_by_id("wnd[0]/tbar[1]/btn[7]").press()  # Display
    bar = session.find_by_id("wnd[0]/sbar")
    failed |= _check(bar, "SE38 nonexistent program", "E", "DS", "017", PROG)

    # Leave the session on the start screen
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/n"
    session.find_by_id("wnd[0]").send_v_key(0)

    print("RESULT:", "FAIL" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
