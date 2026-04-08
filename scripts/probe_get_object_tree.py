"""Empirical probe of SAP GuiSession.GetObjectTree.

Run with the sapsucker dev install and a working SAP environment:

    python scripts/probe_get_object_tree.py

What it does:
1. Connects to a running SAP GUI (or launches/logs-in if not running).
2. Navigates to a known busy screen (BP person create — the canonical
   slow case from sapwebgui.mcp issue #649).
3. Calls session.GetObjectTree("wnd[0]", <all 21 props>) and writes the
   raw JSON to scripts/probe_output_full.json.
4. Calls session.GetObjectTree("wnd[0]") with no props and writes that
   too (scripts/probe_output_ids_only.json) — to confirm the
   "no props == ids only" claim from Stefan Schnell's blog.
5. Calls session.GetObjectTree("wnd[0]", ["Id"]) — minimal props case.
6. Times each call so we have a per-call cost reference.
7. Pretty-prints the top-level JSON structure to stdout (root keys,
   one descent into children, a sample property dict) so we can
   visually verify the shape.
8. For each of the 6 unverified properties, individually checks whether
   it appears in the JSON when requested. (TypeAsNumber, Modified,
   AccText, AccTooltip, AccTextOnRequest, IsSymbolFont)

Output files are written to scripts/ for the issue spec to reference.
NOT a pytest test — manual one-off run.
"""
# pylint: skip-file

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Make sure we use the dev install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # type: ignore[import-not-found]

# Reuse sapwebgui.mcp's config so we don't duplicate credential plumbing.
sys.path.insert(0, "C:/github/sapwebgui.mcp/src")
load_dotenv("C:/github/sapwebgui.mcp/.env")

from sapsucker import SapGui  # noqa: E402
from sapsucker.login import login as sapsucker_login  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
ALL_21_PROPS = [
    "Id",
    "Type",
    "TypeAsNumber",
    "Name",
    "Text",
    "Changeable",
    "Tooltip",
    "DefaultTooltip",
    "IconName",
    "Modified",
    "AccText",
    "AccTooltip",
    "AccTextOnRequest",
    "Height",
    "Width",
    "Left",
    "Top",
    "ScreenLeft",
    "ScreenTop",
    "IsSymbolFont",
    "ContainerType",
]
UNVERIFIED_6 = ["TypeAsNumber", "Modified", "AccText", "AccTooltip", "AccTextOnRequest", "IsSymbolFont"]


def get_session():
    """Connect to SAP GUI; log in fresh if no session is available."""
    from sapwebguimcp.models.config import get_sap_config

    sap_cfg = get_sap_config()
    system = sap_cfg.get_default()

    # Use the sapsucker login helper directly. Reuses any running
    # saplogon.exe; opens a new connection.
    print(f"Logging in to {system.connection_name!r} as {system.user!r}...")
    return sapsucker_login(
        connection_name=system.connection_name,
        client=system.client,
        user=system.user,
        password=system.password.get_secret_value(),
        language=system.language,
    )


def navigate_to_bp_create(session) -> None:
    """Navigate /nBP then F5 (Person create) so we have a busy screen."""
    print("Navigating /nBP...")
    okcd = session.find_by_id("wnd[0]/tbar[0]/okcd")
    okcd.text = "/nBP"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(2)

    # Press F5 (Create Person)
    print("Pressing F5 (Create Person)...")
    session.find_by_id("wnd[0]").send_v_key(5)
    time.sleep(2)

    # Some popups may appear; press Enter to dismiss
    try:
        wnd1 = session.find_by_id("wnd[1]", raise_error=False)
        if wnd1 is not None:
            print("Dismissing popup wnd[1] with Enter...")
            wnd1.send_v_key(0)
            time.sleep(1)
    except Exception as exc:
        print(f"  popup dismiss skipped: {exc!r}")


def call_get_object_tree(session, label: str, *args, **kwargs) -> tuple[str, float]:
    """Call session.GetObjectTree on the COM proxy and time it."""
    print(f"\n=== {label} ===")
    print(f"args={args}  kwargs={kwargs}")
    start = time.perf_counter()
    raw = session.com.GetObjectTree(*args, **kwargs)
    duration_ms = (time.perf_counter() - start) * 1000
    raw_str = str(raw)
    print(f"  duration_ms={duration_ms:.1f}  raw_len={len(raw_str)}")
    return raw_str, duration_ms


def summarize_json(label: str, raw: str) -> None:
    print(f"\n=== {label} (summary) ===")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"  NOT JSON: {exc}")
        print(f"  first 500 chars: {raw[:500]!r}")
        return

    print(f"  type(root): {type(parsed).__name__}")
    if isinstance(parsed, dict):
        print(f"  root keys: {sorted(parsed.keys())}")
        if "properties" in parsed:
            props = parsed["properties"]
            print(f"  root.properties keys: {sorted(props.keys()) if isinstance(props, dict) else type(props).__name__}")
        if "children" in parsed:
            children = parsed["children"]
            print(
                f"  root.children: type={type(children).__name__}  "
                f"len={len(children) if hasattr(children, '__len__') else '?'}"
            )
            if isinstance(children, list) and children:
                first = children[0]
                print(f"  root.children[0] type: {type(first).__name__}")
                if isinstance(first, dict):
                    print(f"  root.children[0] keys: {sorted(first.keys())}")
                    if "properties" in first:
                        fp = first["properties"]
                        if isinstance(fp, dict):
                            print(f"  root.children[0].properties keys: {sorted(fp.keys())}")
                            print("  root.children[0].properties values (sample):")
                            for k, v in list(fp.items())[:8]:
                                print(f"    {k!r}: {v!r}")


def count_elements(parsed: dict) -> int:
    if not isinstance(parsed, dict):
        return 0
    total = 1 if "properties" in parsed else 0
    children = parsed.get("children", [])
    if isinstance(children, list):
        for c in children:
            total += count_elements(c)
    return total


def collect_all_property_keys(parsed: dict, seen: set[str]) -> None:
    """Walk the parsed JSON and collect every property key seen on any node."""
    if not isinstance(parsed, dict):
        return
    props = parsed.get("properties")
    if isinstance(props, dict):
        seen.update(props.keys())
    children = parsed.get("children", [])
    if isinstance(children, list):
        for c in children:
            collect_all_property_keys(c, seen)


def main() -> int:
    session = get_session()
    print(f"Got session: {session.id}")

    navigate_to_bp_create(session)

    # 1. Full props request — the canonical fast-path call
    raw_full, dur_full = call_get_object_tree(
        session, "FULL (all 21 props)", "wnd[0]", ALL_21_PROPS
    )
    (OUT_DIR / "probe_output_full.json").write_text(raw_full, encoding="utf-8")
    print(f"  wrote {OUT_DIR / 'probe_output_full.json'}")
    parsed_full = json.loads(raw_full)
    summarize_json("FULL", raw_full)
    print(f"  total elements (recursive count): {count_elements(parsed_full)}")

    # Which properties actually appear?
    seen_keys: set[str] = set()
    collect_all_property_keys(parsed_full, seen_keys)
    print(f"\n  All distinct property keys seen across all nodes: {sorted(seen_keys)}")
    requested = set(ALL_21_PROPS)
    missing = requested - seen_keys
    extra = seen_keys - requested
    print(f"  REQUESTED but NOT SEEN: {sorted(missing) if missing else '(none)'}")
    print(f"  SEEN but NOT REQUESTED: {sorted(extra) if extra else '(none)'}")

    # Specifically the 6 unverified ones
    print("\n  Unverified-6 status:")
    for prop in UNVERIFIED_6:
        present = prop in seen_keys
        print(f"    {prop}: {'PRESENT' if present else 'MISSING'}")

    # 2. No-props call — should return IDs only
    raw_ids, dur_ids = call_get_object_tree(session, "IDS-ONLY (no props arg)", "wnd[0]")
    (OUT_DIR / "probe_output_ids_only.json").write_text(raw_ids, encoding="utf-8")
    summarize_json("IDS-ONLY", raw_ids)

    # 3. One-prop call — minimal payload reference
    raw_min, dur_min = call_get_object_tree(session, "MINIMAL (Id only)", "wnd[0]", ["Id"])
    (OUT_DIR / "probe_output_min.json").write_text(raw_min, encoding="utf-8")

    # Final summary table
    print("\n=== SUMMARY ===")
    print(f"  FULL (21 props):      {dur_full:6.1f} ms   {len(raw_full):>8} bytes")
    print(f"  IDS-ONLY (no props):  {dur_ids:6.1f} ms   {len(raw_ids):>8} bytes")
    print(f"  MINIMAL (1 prop):     {dur_min:6.1f} ms   {len(raw_min):>8} bytes")
    print(
        "\n  ELEMENT COUNT:",
        count_elements(parsed_full),
        "  (cost per element for FULL: ",
        f"{dur_full / max(count_elements(parsed_full), 1):.3f} ms)",
    )

    # Cleanup: F3 a few times
    try:
        for _ in range(5):
            session.find_by_id("wnd[0]").send_v_key(3)
            time.sleep(0.3)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
