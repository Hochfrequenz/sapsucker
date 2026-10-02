"""Live check of the GuiStatusbar message fields (issue #90).

Needs a running SAP GUI with scripting enabled and at least one open session.
Sends a non-existent transaction code, which makes the system raise a standard
error message, then reads every status-bar message member and reports it.

    uv run python scripts/probe_statusbar_message.py

Exit code 0 only if message_type is "E" and message_id and message_number are
non-empty after the bad transaction. Anything else (including an exception
reading a member) exits 1. Paste the full output back: the values themselves
are what is being established. MessageParameter is probed raw, as both a call
and an index, because its signature is not established anywhere in this repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sapsucker import SapGui  # noqa: E402


def main() -> int:
    session = SapGui.connect().active_session
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nZZNOSUCHTX"
    session.find_by_id("wnd[0]").send_v_key(0)
    bar = session.find_by_id("wnd[0]/sbar")

    failed = False
    print(f"text: {bar.text!r}")
    for name in ("message_type", "message_id", "message_number", "message_as_popup", "message_has_long_text"):
        try:
            value = getattr(bar, name)
            print(f"{name}: {value!r} ({type(value).__name__})")
        except Exception as exc:  # noqa: BLE001 - a probe must report, not crash
            failed = True
            print(f"{name}: RAISED {type(exc).__name__}: {exc}")

    # Raw probe of the unwrapped member: which calling convention works?
    com = bar._com
    for label, getter in (
        ("MessageParameter(0)", lambda: com.MessageParameter(0)),
        ("MessageParameter[0]", lambda: com.MessageParameter[0]),
        ("MessageParameter", lambda: com.MessageParameter),
    ):
        try:
            print(f"{label}: {getter()!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: RAISED {type(exc).__name__}: {exc}")

    try:
        if bar.message_type != "E" or not bar.message_id or not bar.message_number:
            failed = True
            print("FAIL: expected message_type 'E' with non-empty id and number")
    except Exception:  # noqa: BLE001
        failed = True
    print("RESULT:", "FAIL" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
