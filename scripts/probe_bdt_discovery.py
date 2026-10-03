"""Live check of BDT field discovery (issue #89).

Needs a running SAP GUI with scripting enabled and at least one open session.
Opens transaction BP, presses F5 (create person), dumps the user area through the
slow path (``dump_tree(use_fast_path=False)``, the one that calls ``_probe_bdt_fields``)
and prints the number of discovered elements per type. Only type names and counts are
printed, never field values. Nothing is saved: the script leaves with /n and, if a
"data will be lost" popup appears, answers it with No/discard.
Run it in a dedicated session: on such a popup it answers No and may leave the session
in transaction BP.

    uv run python scripts/probe_bdt_discovery.py

It also calls ``FindAllByNameEx("*", n)`` directly on the user area for every probed
type number and prints the hit counts, which is what ``_probe_bdt_fields`` relies on.
Exit codes: 0 = ran and the wildcard queries find elements (the probe can discover
something on this SAP GUI version); 2 = ran and the wildcard queries find nothing;
1 = an uncaught exception (the script did not complete).
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sapsucker import SapGui  # noqa: E402
from sapsucker._types import GuiComponentType  # noqa: E402
from sapsucker.components.base import _BDT_PROBE_TYPES  # noqa: E402
from sapsucker.models import ElementInfo  # noqa: E402


def _walk(tree: list[ElementInfo], counts: Counter[str]) -> None:
    for el in tree:
        counts[el.type or f"type#{el.type_as_number}"] += 1
        _walk(el.children, counts)


def _wait_idle(session: object) -> None:
    for _ in range(100):
        if not session.busy:  # type: ignore[attr-defined]
            return
        time.sleep(0.1)


def _close_popups(session: object) -> None:
    """Dismiss popups without saving: prefer No/discard, else cancel."""
    for _ in range(3):
        try:
            session.find_by_id("wnd[1]")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - no popup
            return
        for btn in ("wnd[1]/usr/btnSPOP-OPTION2", "wnd[1]/usr/btnBUTTON_2", "wnd[1]/tbar[0]/btn[12]"):
            try:
                session.find_by_id(btn).press()  # type: ignore[attr-defined]
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            print("WARNING: popup present, could not dismiss it")
            return
        _wait_idle(session)


def main() -> int:
    app = SapGui.connect()
    try:
        session = app.active_session
    except Exception:  # no SAP GUI window has focus; fall back to the first session
        session = app.connections[0].sessions[0]

    try:
        session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nBP"
        session.find_by_id("wnd[0]").send_v_key(0)
        _wait_idle(session)
        _close_popups(session)
        session.find_by_id("wnd[0]").send_v_key(5)  # F5 = create person
        _wait_idle(session)
        _close_popups(session)

        usr = session.find_by_id("wnd[0]/usr")
        tree = usr.dump_tree(use_fast_path=False)  # type: ignore[attr-defined]
        counts: Counter[str] = Counter()
        _walk(tree, counts)
        print("elements total:", sum(counts.values()))
        for name, n in sorted(counts.items()):
            print(f"  {name}: {n}")
        wildcard_hits = 0
        for type_num in _BDT_PROBE_TYPES:
            hits = int(usr._com.FindAllByNameEx("*", int(type_num)).Count)  # type: ignore[attr-defined]
            wildcard_hits += hits
            print(f"FindAllByNameEx('*', {int(type_num)}) on wnd[0]/usr: {hits} hits")
    finally:
        try:
            session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/n"
            session.find_by_id("wnd[0]").send_v_key(0)
            _wait_idle(session)
            _close_popups(session)
        except Exception as exc:  # noqa: BLE001 - do not mask the original error
            print(f"WARNING: cleanup failed: {exc}")

    radios = counts[GuiComponentType.GuiRadioButton.name]
    labels = counts[GuiComponentType.GuiLabel.name]
    print(f"radio buttons: {radios}, labels: {labels}, wildcard hits: {wildcard_hits}")
    ok = wildcard_hits > 0
    print("RESULT:", "OK" if ok else "NO HITS (wildcard FindAllByNameEx returned nothing)")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
