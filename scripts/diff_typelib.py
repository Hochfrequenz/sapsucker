"""Compare the live COM type library against what sapsucker wraps.

Coverage tooling, not part of the package. Run beside a typelib.json produced by
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

# Anchored to this file, not to the working directory: run from anywhere.
SRC = Path(__file__).resolve().parent.parent / "src" / "sapsucker"
TKIND_COCLASS = 5  # oaidl.h TYPEKIND
BASE_INTERFACES = ("ISapComponentTarget", "ISapVContainerTarget", "ISapContainerTarget", "ISapShell")

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
    # `seen or set()` would discard an explicitly passed empty set, so a caller
    # that supplies one to inspect afterwards would see it stay empty.
    if seen is None:
        seen = set()
    if cls in seen or cls not in table:
        return set()
    seen.add(cls)
    members, bases = table[cls]
    result = set(members)
    for base in bases:
        result |= flattened(base, table, seen)
    return result


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in {"-h", "--help"}:
        # `-h` passes a bare arity check and is then opened as a filename.
        print(__doc__, file=sys.stderr)
        return 2
    if not SRC.is_dir():
        # Without this, SRC.rglob finds nothing, every class reports have=0 and
        # "[not defined in sapsucker]", and the script exits 0 — a plausible,
        # total, silent wrong answer.
        print(f"{SRC} not found — is this a full checkout?", file=sys.stderr)
        return 2
    dump = Path(sys.argv[1])
    # Same standard as the SRC check above: a mistyped path or a JSON that is
    # not a typelib dump should say so, not raise FileNotFoundError or KeyError.
    if not dump.is_file():
        print(f"{dump} not found — pass the JSON written by dump_type_library.py", file=sys.stderr)
        return 2
    try:
        data = json.loads(dump.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{dump} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict) or not isinstance(data.get("types"), dict):
        print(f"{dump} has no 'types' object — is it a dump_type_library.py output?", file=sys.stderr)
        return 2
    types = data["types"]
    ours = sapsucker_classes()

    # The type library flattens inheritance, so every class repeats the
    # GuiComponent / GuiVComponent / GuiShell surface. Subtract those to leave
    # each class's own members, which is what a coverage question is about.
    inherited: set[str] = set()
    for base_iface in BASE_INTERFACES:
        inherited |= set(types.get(base_iface, {}).get("members", {}))
    absent = [b for b in BASE_INTERFACES if not types.get(b, {}).get("members")]
    if absent:
        # Otherwise the header prints a confident "N member names" over a
        # subtraction that silently did nothing.
        print(f"base interfaces missing from the dump: {', '.join(absent)}", file=sys.stderr)
        return 2

    coclasses = sorted(k for k, v in types.items() if k.startswith("Gui") and v.get("typekind") == TKIND_COCLASS)
    # An entry the dumper could not read has no "typekind" at all, so it would
    # otherwise land in `skipped` and be reported as "not a coclass" — a benign
    # label over a dump failure.
    errored = sorted(k for k, v in types.items() if k.startswith("Gui") and "error" in v)
    skipped = sorted(
        k for k, v in types.items() if k.startswith("Gui") and "error" not in v and v.get("typekind") != TKIND_COCLASS
    )
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

    print("own members only — the GuiComponent/VContainer/Container/Shell surface is subtracted")
    print(f"(inherited surface subtracted: {len(inherited)} member name(s))\n")
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
    measured = {cls for cls, *_ in rows}
    for cls, iface, _n_live, _n_have, missing, _d in rows:
        if cls in interest:
            print(f"\n{cls}  ({iface})  {len(missing)} not reached:")
            print("  " + ", ".join(missing) if missing else "  (none)")
    for cls in interest:
        if cls not in measured:
            # Otherwise a class asked about by name is simply absent, which reads
            # as "no gaps". GuiToolbar is exactly this case — no interface of its
            # own matched, so nothing was measured.
            print(f"\n{cls}  NOT MEASURED — no interface matched, so this is not a report of zero gaps")

    if errored:
        print(f"\n=== Gui* types the dump could not read, so not compared: {', '.join(errored)}")
        for cls in errored:
            print(f"    {cls}: {types[cls]['error']}")
    if skipped:
        print(f"\n=== Gui* types that are not coclasses, so not compared: {', '.join(skipped)}")
    if unmapped:
        print(f"\n=== no interface matched (naming convention miss): {', '.join(unmapped)}")
    return 2 if errored else 0


if __name__ == "__main__":
    sys.exit(main())
