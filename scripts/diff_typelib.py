"""Compare the live COM type library against what sapsucker wraps.

SPIKE PROBE — not package code. Run beside a typelib.json produced by
``dump_type_library.py``:

    uv run python scripts/diff_typelib.py typelib.json

Answers "what does this SAP GUI expose that sapsucker does not reach?" — the
authoritative version of the question, for the installed release, covering
properties as well as methods.

Two things to know when reading the output:

* The type library names coclasses ``GuiXxx`` (no members) and dispinterfaces
  ``ISapXxxTarget`` or ``ISapXxx`` (all the members). The suffix is not uniform,
  so several spellings are tried per class.
* Interface member lists are FLATTENED — they include everything inherited from
  GuiVComponent, GuiShell and so on. So sapsucker's side is flattened too, over
  each class's own MRO, or the comparison would invent gaps that do not exist.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SRC = Path("src/sapsucker")

# Members every COM object carries; noise in a coverage report.
COM_NOISE = {
    "QueryInterface",
    "AddRef",
    "Release",
    "GetTypeInfoCount",
    "GetTypeInfo",
    "GetIDsOfNames",
    "Invoke",
    "GetTypeLib",
}


def interface_candidates(cls: str) -> list[str]:
    stem = cls[3:] if cls.startswith("Gui") else cls
    return [f"ISap{stem}Target", f"ISap{stem}", f"IGui{stem}Target", f"IGui{stem}"]


def sapsucker_classes() -> dict[str, tuple[set[str], list[str]]]:
    """class -> (COM members it touches, base class names)."""
    out: dict[str, tuple[set[str], list[str]]] = {}
    for path in SRC.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        # `(...)` optional: GuiComponent and GuiSessionInfo declare no bases, and
        # skipping them dropped Id/Name/Type/TypeAsNumber/Parent out of every
        # descendant's flattened set — which then showed up as phantom gaps.
        marks = [
            (m.group(1), m.group(2) or "", m.start())
            for m in re.finditer(r"^class\s+(Gui\w+)(?:\(([^)]*)\))?", src, re.M)
        ]
        for i, (cls, bases, pos) in enumerate(marks):
            end = marks[i + 1][2] if i + 1 < len(marks) else len(src)
            members = {m.group(1) for m in re.finditer(r"_com\.(\w+)", src[pos:end])}
            out[cls] = (members, [b.strip() for b in bases.split(",") if b.strip().startswith("Gui")])
    return out


def flattened(cls: str, table: dict[str, tuple[set[str], list[str]]], seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if cls in seen or cls not in table:
        return set()
    seen.add(cls)
    members, bases = table[cls]
    result = set(members)
    for base in bases:
        result |= flattened(base, table, seen)
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    types = data["types"]
    ours = sapsucker_classes()

    # The type library flattens inheritance, so every class repeats the
    # GuiComponent / GuiVComponent / GuiShell surface. Subtract those to leave
    # each class's own members, which is what a coverage question is about.
    inherited: set[str] = set()
    for base_iface in ("ISapComponentTarget", "ISapVContainerTarget", "ISapContainerTarget", "ISapShell"):
        inherited |= set(types.get(base_iface, {}).get("members", {}))

    coclasses = sorted(k for k, v in types.items() if k.startswith("Gui") and not v.get("members"))
    rows, unmapped = [], []
    for cls in coclasses:
        iface = next((c for c in interface_candidates(cls) if types.get(c, {}).get("members")), None)
        if iface is None:
            unmapped.append(cls)
            continue
        exposed = {
            m for m in types[iface]["members"] if m not in COM_NOISE and not m.startswith("_") and m not in inherited
        }
        have = flattened(cls, ours)
        missing = sorted(exposed - have)
        rows.append((cls, iface, len(exposed), len(exposed) - len(missing), missing, cls in ours))

    print("own members only — the GuiComponent/VComponent/Container/Shell surface is subtracted")
    print(f"(inherited surface subtracted: {len(inherited)} member names)\n")
    print(f"{'class':24s} {'live':>5s} {'have':>5s} {'gap':>4s}  interface")
    print("-" * 78)
    for cls, iface, n_live, n_have, missing, defined in sorted(rows, key=lambda r: -len(r[4])):
        flag = "" if defined else "  [not defined in sapsucker]"
        print(f"{cls:24s} {n_live:5d} {n_have:5d} {len(missing):4d}  {iface}{flag}")

    print("\n=== full gap lists for classes of interest ===")
    interest = [
        "GuiCalendar",
        "GuiPicture",
        "GuiSplit",
        "GuiComboBoxControl",
        "GuiInputFieldControl",  # issue #41
        "GuiToolbar",
        "GuiStatusbar",
        "GuiComboBox",
        "GuiGridView",
    ]
    for cls, iface, _n_live, _n_have, missing, _d in rows:
        if cls in interest:
            print(f"\n{cls}  ({iface})  {len(missing)} not reached:")
            print("  " + ", ".join(missing) if missing else "  (none)")

    if unmapped:
        print(f"\n=== no interface matched (naming convention miss): {', '.join(unmapped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
