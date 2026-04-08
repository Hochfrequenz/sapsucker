"""Real-SAP parity test: fast path vs slow path of ``GuiVContainer.dump_tree``.

This is the strongest correctness gate for the GetObjectTree fast path
introduced in sapsucker #20. It runs against a live SAP system, captures
the element tree via BOTH paths, and asserts they are bit-for-bit
identical across all 20 comparable fields of every element.

Skipped unless:
- Running on Windows (COM is Windows-only)
- Running on the authorized SAP test machine
- SAP credentials are configured in .env

The test logs into SAP (auto-launches SAP Logon if needed), navigates
to the BP person create screen (a 270+ element BDT-heavy screen — the
canonical "busy" case), captures the slow path output, then captures
the fast path output, and asserts equality element-by-element.

If this test ever fails, it means the fast path is producing different
ElementInfo shapes than the slow path on a real SAP screen — which is
a regression that the unit tests cannot catch (they use mock COM,
which silently falls through to the slow path).
"""

# pylint: disable=protected-access

from __future__ import annotations

import sys
import time

import pytest

from unittests.conftest import is_sap_integration_test_machine

# Skip everything on non-Windows
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="SAP GUI COM is Windows-only")

skip_not_sap_machine = pytest.mark.skipif(
    not is_sap_integration_test_machine(),
    reason="SAP integration tests only run on authorized machines",
)

# All comparable fields between fast and slow paths. Order does not matter.
_COMPARED_FIELDS = (
    "id",
    "type",
    "type_as_number",
    "name",
    "text",
    "changeable",
    "tooltip",
    "default_tooltip",
    "icon_name",
    "modified",
    "acc_text",
    "acc_tooltip",
    "acc_text_on_request",
    "height",
    "width",
    "left",
    "top",
    "screen_left",
    "screen_top",
    "is_symbol_font",
    "container_type",
)


def _flatten(elements):
    """Flatten a recursive ElementInfo tree into a flat list."""
    out = []
    for elem in elements:
        out.append(elem)
        out.extend(_flatten(elem.children))
    return out


def _assert_parity(scenario: str, wnd, *, min_elements: int = 1) -> int:
    """Run dump_tree via both paths against *wnd* and assert bit-for-bit parity.

    Returns the element count so individual scenarios can also assert
    "this screen had the expected order-of-magnitude size".

    The same comparison logic is reused across all parity scenarios so
    failures look identical regardless of which screen they came from —
    the only thing that varies is the *scenario* label embedded in
    failure messages.
    """
    # Avoid an import-time COM dependency in non-SAP environments.
    from sapsucker.components.base import _dump_tree_recursive  # pylint: disable=import-outside-toplevel

    # SLOW path first — the historical baseline. We do it FIRST so that
    # if SAP state drifts mid-test (which it sometimes does on busy
    # screens), the slow path captures the original tree and the fast
    # path is asked to match THAT, not vice versa.
    slow_tree = _dump_tree_recursive(wnd.com, 0, 200)

    # FAST path: GuiVContainer.dump_tree (which tries the fast path
    # via GuiSession.GetObjectTree first, falls back to slow path on error)
    fast_tree = wnd.dump_tree()

    slow_flat = _flatten(slow_tree)
    fast_flat = _flatten(fast_tree)

    # Total counts must match exactly
    assert len(slow_flat) == len(fast_flat), (
        f"[{scenario}] Element count mismatch: slow={len(slow_flat)} vs fast={len(fast_flat)}. "
        f"This means the two dump_tree paths see different numbers of elements "
        f"on the same screen — the fast path is missing or duplicating elements."
    )
    assert len(slow_flat) >= min_elements, (
        f"[{scenario}] Expected at least {min_elements} elements, got {len(slow_flat)}. "
        f"Either navigation did not reach the expected screen or it changed shape."
    )

    # Every ID must be present in both
    slow_by_id = {e.id: e for e in slow_flat}
    fast_by_id = {e.id: e for e in fast_flat}
    slow_only = set(slow_by_id) - set(fast_by_id)
    fast_only = set(fast_by_id) - set(slow_by_id)
    assert not slow_only, (
        f"[{scenario}] {len(slow_only)} element ID(s) present in slow path but missing from fast path. "
        f"First 5: {sorted(slow_only)[:5]}. "
        f"This means the fast path is silently dropping elements that the slow path "
        f"discovers (most likely BDT-injected fields or screen-type-specific containers)."
    )
    assert not fast_only, (
        f"[{scenario}] {len(fast_only)} element ID(s) present in fast path but missing from slow path. "
        f"First 5: {sorted(fast_only)[:5]}. "
        f"This is the opposite problem — the fast path is reporting phantom elements."
    )

    # For every common ID, every comparable field must match exactly
    field_diffs: dict[str, list[tuple[str, object, object]]] = {f: [] for f in _COMPARED_FIELDS}
    for elem_id, slow_elem in slow_by_id.items():
        fast_elem = fast_by_id[elem_id]
        for field in _COMPARED_FIELDS:
            slow_value = getattr(slow_elem, field)
            fast_value = getattr(fast_elem, field)
            if slow_value != fast_value:
                field_diffs[field].append((elem_id, slow_value, fast_value))

    total_diffs = sum(len(v) for v in field_diffs.values())
    if total_diffs > 0:
        # Build a readable failure message
        lines = [f"[{scenario}] Field-level diffs found: {total_diffs} total"]
        for field, diffs in field_diffs.items():
            if not diffs:
                continue
            lines.append(f"  {field}: {len(diffs)} diffs")
            for elem_id, slow_v, fast_v in diffs[:3]:
                lines.append(f"    {elem_id[:80]}... slow={slow_v!r} fast={fast_v!r}")
        pytest.fail("\n".join(lines))

    return len(slow_flat)


def _navigate_home(session) -> None:
    """Press F3 a few times to get back to SAP Easy Access between scenarios."""
    for _ in range(6):
        try:
            session.find_by_id("wnd[0]").send_v_key(3)
            time.sleep(0.3)
        except Exception:
            break
    # Dismiss any "are you sure?" popup that F3 may trigger.
    try:
        popup = session.find_by_id("wnd[1]", raise_error=False)
        if popup is not None:
            popup.send_v_key(0)
            time.sleep(0.3)
    except Exception:
        pass


@skip_not_sap_machine
def test_parity_bp_person_create(sap_desktop_session):
    """Parity on the canonical "busy" screen — BP person create.

    277-element BDT-heavy screen — the original repro from issue #20.
    The slow path historically takes ~5–11 seconds here while the fast
    path takes ~200ms. Asserts every element ID matches and every
    comparable field matches across all 20 fields × ~270 elements.

    This is the strongest correctness gate for the new fast path on
    BDT-heavy screens.
    """
    session = sap_desktop_session

    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nBP"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(2)
    session.find_by_id("wnd[0]").send_v_key(5)  # F5 (Create Person)
    time.sleep(2)
    # Some test systems pop a confirmation; press Enter to dismiss.
    popup = session.find_by_id("wnd[1]", raise_error=False)
    if popup is not None:
        popup.send_v_key(0)
        time.sleep(1)

    wnd = session.find_by_id("wnd[0]")
    count = _assert_parity("BP person create", wnd, min_elements=50)
    assert count > 200, f"Expected BP create screen to have >200 elements, got {count}"

    _navigate_home(session)


@skip_not_sap_machine
def test_parity_se38_main_screen(sap_desktop_session):
    """Parity on a small/empty screen — SE38 main screen (ABAP Editor entry).

    Tests the opposite end of the size spectrum from BP person create:
    a screen with only ~30–60 elements, no BDT containers, no table
    controls. If the fast path mishandles small/sparse trees in any
    way, this catches it.
    """
    session = sap_desktop_session

    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nSE38"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(2)

    wnd = session.find_by_id("wnd[0]")
    _assert_parity("SE38 main screen", wnd, min_elements=10)

    _navigate_home(session)


@skip_not_sap_machine
def test_parity_se16_table_browser(sap_desktop_session):
    """Parity on a screen with table-control / GuiCtrlGridView elements.

    SE16 (data browser) → enter standard table TSTC → F8 → results.
    The result screen has a GuiCtrlGridView (ALV grid) shell with
    rows/cells that may or may not be in the standard Children tree.
    If the fast path handles these differently than the slow path, the
    parity check fails — this is the canonical "ALV grid" case the
    reviewer flagged as not covered by BP person create.
    """
    session = sap_desktop_session

    # Navigate to SE16 selection screen
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nSE16"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(2)

    # Enter table name TSTC (a standard SAP table that exists on every
    # SAP system; small enough to display quickly).
    try:
        tname = session.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME", raise_error=False)
        if tname is not None:
            tname.text = "TSTC"
            session.find_by_id("wnd[0]").send_v_key(0)  # Enter
            time.sleep(1)
            session.find_by_id("wnd[0]").send_v_key(8)  # F8 (Execute)
            time.sleep(2)
        else:
            pytest.skip("SE16 selection screen field not found on this SAP version")
    except Exception as exc:
        pytest.skip(f"SE16 navigation failed: {exc}")

    # Some SAP systems pop a "no entries / many entries" warning; dismiss.
    popup = session.find_by_id("wnd[1]", raise_error=False)
    if popup is not None:
        popup.send_v_key(0)
        time.sleep(1)

    wnd = session.find_by_id("wnd[0]")
    _assert_parity("SE16 TSTC results (ALV grid)", wnd, min_elements=10)

    _navigate_home(session)


def _try_trigger_popup(session) -> object | None:
    """Try several SAP-version-independent ways to raise a wnd[1] popup.

    Different SAP versions / customizations behave differently. We try
    each strategy in order and return the first wnd[1] proxy we get.
    Returns None if nothing worked — the caller should pytest.skip.
    """
    strategies = [
        # SE16 with no table name — usually pops "Enter the table name"
        ("/nSE16 with empty table → F8", lambda: _try_se16_empty_table(session)),
        # BP person create flow — the "Treat business partner..." dialog
        ("/nBP F5 → confirmation popup", lambda: _try_bp_create_popup(session)),
        # SE38 with empty program → F8 — sometimes pops, sometimes status-bar
        ("/nSE38 F8 with empty program", lambda: _try_se38_empty_program(session)),
    ]
    for name, attempt in strategies:
        try:
            popup = attempt()
            if popup is not None:
                return popup
        except Exception:
            pass
        # Reset to easy access between attempts
        _navigate_home(session)
        time.sleep(0.5)
    return None


def _try_se16_empty_table(session):
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nSE16"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(1.5)
    # Clear the table-name field so F8 has nothing to execute on
    tname = session.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME", raise_error=False)
    if tname is not None:
        tname.text = ""
    session.find_by_id("wnd[0]").send_v_key(8)  # F8 (Execute)
    time.sleep(1)
    return session.find_by_id("wnd[1]", raise_error=False)


def _try_bp_create_popup(session):
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nBP"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(2)
    session.find_by_id("wnd[0]").send_v_key(5)  # F5 — Create Person
    time.sleep(2)
    return session.find_by_id("wnd[1]", raise_error=False)


def _try_se38_empty_program(session):
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nSE38"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(1.5)
    pname = session.find_by_id("wnd[0]/usr/ctxtRS38M-PROGRAMM", raise_error=False)
    if pname is not None:
        pname.text = ""
    session.find_by_id("wnd[0]").send_v_key(8)  # F8
    time.sleep(1)
    return session.find_by_id("wnd[1]", raise_error=False)


@skip_not_sap_machine
def test_parity_modal_popup_window(sap_desktop_session):
    """Parity on a wnd[1] modal popup, dumping the popup itself (not wnd[0]).

    Tests the case where the queried element is a GuiModalWindow rather
    than the GuiMainWindow — different SAP class, different tree shape,
    typically smaller tree.

    sapwebgui.mcp frequently calls dump_tree on wnd[1] for popup
    handling, so any divergence here would propagate to LLM tool
    calls touching popups. Tries several popup-triggering strategies
    in order so the test is robust across SAP versions.
    """
    session = sap_desktop_session

    popup = _try_trigger_popup(session)
    if popup is None:
        pytest.skip(
            "Could not trigger any wnd[1] popup using the standard strategies "
            "(SE16 empty table, BP F5 confirmation, SE38 empty program). "
            "This SAP version may suppress all of them."
        )

    try:
        _assert_parity("wnd[1] modal popup", popup, min_elements=1)
    finally:
        # Dismiss the popup so the next test starts clean
        try:
            popup.send_v_key(0)  # Enter / OK
            time.sleep(0.5)
        except Exception:
            pass
        _navigate_home(session)
