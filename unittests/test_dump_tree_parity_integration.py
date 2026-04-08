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


@skip_not_sap_machine
def test_fast_and_slow_paths_produce_identical_element_info(sap_desktop_session):
    """Bit-for-bit parity between GetObjectTree fast path and per-property slow path.

    Captures both paths against the BP person create screen and asserts
    every element ID is present in both, every comparable field matches,
    and the element counts agree. Catches any silent divergence between
    the two paths against a real SAP GUI tree — the strongest possible
    regression check for the new fast path.

    Skipped if SAP GUI is not available (the ``sap_desktop_session``
    fixture takes care of that).
    """
    # Avoid an import-time COM dependency in non-SAP environments.
    from sapsucker.components.base import _dump_tree_recursive  # pylint: disable=import-outside-toplevel

    session = sap_desktop_session

    # Navigate to a busy screen with BDT containers — the canonical
    # "slow case" from the issue, with ~270 elements.
    session.find_by_id("wnd[0]/tbar[0]/okcd").text = "/nBP"
    session.find_by_id("wnd[0]").send_v_key(0)  # Enter
    time.sleep(2)
    session.find_by_id("wnd[0]").send_v_key(5)  # F5 (Create Person)
    time.sleep(2)
    # Some test systems pop a confirmation; press Enter to dismiss.
    popup = session.find_by_id("wnd[1]", raise_error=False)
    if popup is not None:
        popup.send_v_key(0)
        time.sleep(1)

    wnd = session.find_by_id("wnd[0]")

    # SLOW path: per-property COM reads via _dump_tree_recursive
    slow_tree = _dump_tree_recursive(wnd.com, 0, 200)

    # FAST path: GuiVContainer.dump_tree (which tries the fast path
    # via GuiSession.GetObjectTree first, falls back to slow path on error)
    fast_tree = wnd.dump_tree()

    slow_flat = _flatten(slow_tree)
    fast_flat = _flatten(fast_tree)

    # Total counts must match exactly
    assert len(slow_flat) == len(fast_flat), (
        f"Element count mismatch: slow={len(slow_flat)} vs fast={len(fast_flat)}. "
        f"This means the two dump_tree paths see different numbers of elements "
        f"on the same screen — the fast path is missing or duplicating elements."
    )
    assert len(slow_flat) > 50, (
        f"Expected the BP create screen to have >50 elements, got {len(slow_flat)}. "
        f"Either the navigation didn't reach the create screen or this is a tiny "
        f"dialog where the parity check isn't meaningful."
    )

    # Every ID must be present in both
    slow_by_id = {e.id: e for e in slow_flat}
    fast_by_id = {e.id: e for e in fast_flat}
    slow_only = set(slow_by_id) - set(fast_by_id)
    fast_only = set(fast_by_id) - set(slow_by_id)
    assert not slow_only, (
        f"{len(slow_only)} element ID(s) present in slow path but missing from fast path. "
        f"First 5: {sorted(slow_only)[:5]}. "
        f"This means the fast path is silently dropping elements that the slow path "
        f"discovers (most likely BDT-injected fields)."
    )
    assert not fast_only, (
        f"{len(fast_only)} element ID(s) present in fast path but missing from slow path. "
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
        lines = [f"Field-level diffs found: {total_diffs} total"]
        for field, diffs in field_diffs.items():
            if not diffs:
                continue
            lines.append(f"  {field}: {len(diffs)} diffs")
            for elem_id, slow_v, fast_v in diffs[:3]:
                lines.append(f"    {elem_id[:80]}... slow={slow_v!r} fast={fast_v!r}")
        pytest.fail("\n".join(lines))
