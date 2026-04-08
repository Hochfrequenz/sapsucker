# Test fixtures

Real SAP-captured data used as input by unit tests. Each fixture is a snapshot
from a live SAP GUI session, captured by an empirical probe script. They are
checked into the repo so the tests can run against real-shape data without
needing a live SAP environment.

## `get_object_tree_bp_create_*.json`

Output of `GuiSession.GetObjectTree("wnd[0]", ...)` against the SAP transaction
`BP` (Business Partner) → F5 (Create Person) screen on a Hochfrequenz S/4
test system. Captured 2026-04-08 by `scripts/probe_get_object_tree.py`. The
screen has 277 elements and represents a typical "busy" SAP screen — the
canonical slow case from sapwebgui.mcp issue #649.

| File | What it captures | Bytes |
|---|---|---|
| `get_object_tree_bp_create_full.json` | All 21 properties (`Id`, `Type`, `TypeAsNumber`, `Name`, `Text`, `Changeable`, `Tooltip`, `DefaultTooltip`, `IconName`, `Modified`, `AccText`, `AccTooltip`, `AccTextOnRequest`, `Height`, `Width`, `Left`, `Top`, `ScreenLeft`, `ScreenTop`, `IsSymbolFont`, `ContainerType`) — exactly the set sapsucker reads in `_build_element_info` | ~171 KB |
| `get_object_tree_bp_create_ids_only.json` | The "no `props` argument" call form. Per SAP's spec, calling `GetObjectTree(id)` without a `props` array returns only the `Id` property of each node. | ~69 KB |
| `get_object_tree_bp_create_id_only.json` | The same content as `_ids_only.json` but explicitly requested via `props=["Id"]`. Verifies that no-props and `["Id"]` are equivalent. | ~69 KB |

## JSON shape (verified empirically against the BP fixture)

```
root              : dict, keys = ["children"]
root.children     : list, length 1                 ← wraps the queried element
root.children[0]  : dict, keys = ["properties", "children"]
                                                    ← THIS is the queried element (e.g. wnd[0])
root.children[0].properties : dict[str, str]        ← all values are strings,
                                                      even ints and bools
                                                      (e.g. "TypeAsNumber": "21",
                                                            "Changeable": "true")
root.children[0].children   : list                  ← actual descendants
                                                      (also wrapped in this shape recursively)
```

## How to regenerate

```
cd C:/github/sapsucker
python scripts/probe_get_object_tree.py
cp scripts/probe_output_full.json     unittests/fixtures/get_object_tree_bp_create_full.json
cp scripts/probe_output_ids_only.json unittests/fixtures/get_object_tree_bp_create_ids_only.json
cp scripts/probe_output_min.json      unittests/fixtures/get_object_tree_bp_create_id_only.json
```

The probe script needs a working SAP environment with credentials in
`C:/github/sapwebgui.mcp/.env`. These files are intentionally checked in
verbatim — they should NOT be regenerated unless the JSON shape changes
upstream in SAP, in which case the change should be reviewed deliberately
(it would mean the SAP scripting JSON contract changed and we need to
update the parser).
