"""Dump the SAP GUI Scripting COM type library to JSON.

Coverage tooling, not part of the package. Pairs with diff_typelib.py; see
docs/coverage-gaps.md for the findings and their caveats.

Answers "what does *this* SAP GUI actually expose?", as opposed to what the
Scripting API guide documents. The type library is the authoritative list for the
installed version: every coclass and interface with all its methods **and
properties**, including ones the PDF renders in tables that no text extraction
reads cleanly.

Run it on the Windows box and hand over the JSON; the diff against the docs and
against sapsucker's wrappers happens off-box.

    uv run python scripts/dump_type_library.py -o typelib.json

SAP GUI need not have a session open — the type library is static. It does need
to be installed, and pywin32 present.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# INVOKEKIND flags from oaidl.h — a member can be several at once.
INVOKE_FUNC = 1
INVOKE_PROPERTYGET = 2
INVOKE_PROPERTYPUT = 4
INVOKE_PROPERTYPUTREF = 8


def _kind(invkind: int) -> str:
    if invkind & INVOKE_FUNC:
        return "method"
    parts = []
    if invkind & INVOKE_PROPERTYGET:
        parts.append("get")
    if invkind & (INVOKE_PROPERTYPUT | INVOKE_PROPERTYPUTREF):
        parts.append("set")
    return "property(" + "/".join(parts) + ")" if parts else f"unknown({invkind})"


# The two stock SAP GUI install locations. Not exhaustive — that is what
# --typelib is for — so a test that needs the OCX reads this list rather than
# repeating a guess that could drift from the script's.
OCX_CANDIDATES = [
    Path(base) / "sapfewse.ocx"
    for base in (r"C:\Program Files (x86)\SAP\FrontEnd\SAPgui", r"C:\Program Files\SAP\FrontEnd\SAPgui")
]


def _load_type_lib(explicit: Path | None = None) -> Any:
    """Get the type library, preferring a live scripting engine over a file guess."""
    if explicit is not None and not explicit.exists():
        # An explicit path is an instruction, not a hint: fail loudly rather than
        # falling through to a guess and dumping a different installation's
        # library. Checked before the pywin32 import so a typo does not surface
        # as ModuleNotFoundError.
        raise SystemExit(f"{explicit} does not exist")

    import pythoncom  # type: ignore[import-untyped]  # noqa: PLC0415
    import win32com.client  # type: ignore[import-untyped]  # noqa: PLC0415

    if explicit is not None:
        print(f"loading {explicit}", file=sys.stderr)
        return pythoncom.LoadTypeLib(str(explicit))

    try:
        engine = win32com.client.GetObject("SAPGUI").GetScriptingEngine
        type_info = engine._oleobj_.GetTypeInfo()
        lib, _index = type_info.GetContainingTypeLib()
        return lib
    except Exception as exc:
        print(f"could not reach a running scripting engine ({exc}); trying sapfewse.ocx", file=sys.stderr)

    for path in OCX_CANDIDATES:
        if path.exists():
            print(f"loading {path}", file=sys.stderr)
            return pythoncom.LoadTypeLib(str(path))
    raise SystemExit(
        "No type library found. Start SAP GUI (a session is not required), "
        "or pass the path to sapfewse.ocx with --typelib."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", default="typelib.json", type=Path)
    parser.add_argument(
        "--typelib",
        type=Path,
        default=None,
        help="path to sapfewse.ocx, for an installation outside the two default locations",
    )
    args = parser.parse_args()

    lib = _load_type_lib(args.typelib)
    lib_name, lib_doc = lib.GetDocumentation(-1)[:2]
    count = lib.GetTypeInfoCount()
    print(f"type library: {lib_name} — {lib_doc} ({count} type infos)", file=sys.stderr)

    types: dict[str, Any] = {}
    for i in range(count):
        name = lib.GetDocumentation(i)[0]
        try:
            info = lib.GetTypeInfo(i)
            attr = info.GetTypeAttr()
        except Exception as exc:
            types[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue

        members: dict[str, dict[str, Any]] = {}
        for f in range(attr.cFuncs):
            try:
                desc = info.GetFuncDesc(f)
                member = info.GetNames(desc.memid)[0]
                entry = members.setdefault(member, {"kinds": [], "params": None})
                entry["kinds"].append(_kind(desc.invkind))
                if desc.invkind & INVOKE_FUNC:
                    entry["params"] = len(desc.args)
            except Exception:
                continue
        # Some interfaces expose fields (vars) rather than funcs.
        for v in range(attr.cVars):
            try:
                desc = info.GetVarDesc(v)
                members.setdefault(info.GetNames(desc.memid)[0], {"kinds": ["var"], "params": None})
            except Exception:
                continue

        types[name] = {"typekind": attr.typekind, "members": members}

    payload = {"library": {"name": lib_name, "doc": lib_doc}, "types": types}
    args.out.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")

    with_members = {k: v for k, v in types.items() if v.get("members")}
    print(f"wrote {args.out}: {len(types)} types, {len(with_members)} with members", file=sys.stderr)
    gui = sorted(k for k in with_members if k.startswith("Gui"))
    print(f"Gui* types with members: {len(gui)}", file=sys.stderr)
    print("  " + ", ".join(gui[:12]) + (" …" if len(gui) > 12 else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
