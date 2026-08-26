# COM coverage: what sapsucker does not wrap

A snapshot of how much of the SAP GUI Scripting COM API this package reaches, measured against a live installation rather than against SAP's documentation.

**Deliberately a static document, not a CI check.** Producing it needs a Windows machine with SAP GUI installed, which CI does not have, and the caveats below make the numbers unfit as a pass/fail gate. Re-run it by hand occasionally — when bumping supported SAP GUI versions is the natural cadence.

## How to reproduce

```bash
# On Windows, with SAP GUI installed. No session or login required:
# the type library is static.
uv run python scripts/dump_type_library.py -o typelib.json

# Anywhere, with the JSON anywhere:
uv run python scripts/diff_typelib.py typelib.json
```

Read the exit codes. The dumper returns **1** when it had to drop members or
could not read a type at all, and names each one on stderr — it still writes the
file, but every coverage number computed from it is understated, so a partial
dump must not be mistaken for a clean one. The diff returns **2** when its input
is unusable: no `types` object, missing base interfaces to subtract, or `Gui*`
types the dump recorded as errors. Neither script treats a silent partial
success as success.

The pair is checked against a real installation by
`unittests/test_dump_type_library_integration.py`. Its SAP-side tests run only
on Windows with SAP GUI installed, and skip everywhere else — no session and no
login needed, since the type library is static:

```bash
uv run pytest unittests/test_dump_type_library_integration.py -rs -v
```

`-rs` is not optional. A skipped test reads as a pass in the summary line, so if
those tests report `skipped` rather than `passed`, nothing about SAP was checked.

## Why the type library rather than the PDF

The Scripting API guide documents properties in tables that no text extraction reads reliably, so a doc-based diff sees methods only — which would miss, for instance, `GuiComboBox.Key` (a property, and a real gap: see #88).

The type library also describes the version actually installed, so a "gap" cannot turn out to be a member that only exists in a newer SAP GUI. That distinction is not academic: a doc-based pass produced a finding that `GuiToolbar` was missing twelve methods, seven of them `GetButton*`. The type library shows `ISapToolbarTarget` with no own members at all — the guide documents all twelve on `GuiToolbarControl` (§1.2.68), not `GuiToolbar` (§1.2.67), and sapsucker wraps eleven of them there, all seven `GetButton*` included (`src/sapsucker/components/shell.py:104`–`:144`); only `GetMenuItemIdFromPosition` is unwrapped. The finding was never filed, but it would have been wrong.

## Caveats — read before using any number here

- **"Reached" means *touched*, not *properly exposed*.** The diff finds `_com.<Name>` by regex within each class's text region, unioned over its Python bases. It matches the COM name, so a wrapper named differently still counts (`set_cell_value` → `ModifyCell`, `src/sapsucker/components/grid.py:81`) — correct, but it means "reached" says nothing about the Python-side API. It genuinely over-credits where module-level code sits inside a class's text region, which **inflates coverage**. The subtraction below errs in the opposite direction.
- **Only four base interfaces are subtracted**, so a class extending another *wrapped* class reports its parent's surface as its own — `GuiMainWindow` (27) and `GuiModalWindow` (23) carry `GuiFrameWindow`'s. This **overstates the gap**. (`GuiCTextField` and `GuiPasswordField` were listed here as reporting "`GuiTextField`'s 20"; that was wrong twice over. `GuiTextField` reports 23, not 20, and the 20 comes from resolving to a different interface entirely — see below.)
- **Own-member counts are approximate.** An inherited surface of 43 names, taken from four base interfaces, is subtracted so each class shows its own members. A class that legitimately redeclares an inherited member is under-counted.
- **13 coclasses are unmeasured.** No candidate interface (`GuiXxx` → `ISapXxxTarget` / `ISapXxx`) resolved to a non-empty member list for `GuiApplication`, `GuiComponentCollection`, `GuiContainerShell`, `GuiEnum`, `GuiFrameWindow`, `GuiGOSShell`, `GuiSapChart`, `GuiStatusBarLink`, `GuiTabStrip`, `GuiTitlebar`, `GuiUserArea`, `GuiVComponent`, `GuiViewSwitchTarget`. `GuiApplication` and `GuiFrameWindow` matter.
- **"Not reached" means not reachable *on this class*.** Several names sit in one class's gap list while being wrapped on another. `Click`, `DoubleClick` and `ContextMenu` are gaps on `GuiPicture` (and `ContextMenu` on `GuiCalendar`, `DoubleClick` on `GuiStatusbar`) while all three are wrapped on `GuiGridView` — `src/sapsucker/components/grid.py:97`, `:101`, `:153`. `Entries` and `Selected` are gaps on `GuiComboBoxControl` while being wrapped on `GuiComboBox` (`src/sapsucker/components/combobox.py:57`) and `GuiCheckBox` (`src/sapsucker/components/checkbox.py:16`).
- **Declared is not working.** A member in the type library may be blocked by read-only scripting mode, unpopulated, or non-functional on a given release.
- **One installation, one version** — and now it says which. This snapshot is `sapfewse.ocx` file version **8000.1.4.257**, from `C:\Program Files\SAP\FrontEnd\SAPgui\`, type library `SAPFEWSELib` with 161 type infos. Every number below is that installation's.

  Read the raw file version, not a release name: the dump records `sapgui_version` verbatim rather than mapping it to a marketing version, because that mapping is a convention nobody here has verified. Note also that the type library reports its *own* version as `1.0` and has done across releases — it is recorded as `typelib_version` and identifies nothing.

## Where the large gaps are

Own members, reached / total:

| Class | Reached | Own | Note |
| --- | --- | --- | --- |
| `GuiAbapEditor` | 2 | 108 | largest gap in the package |
| `GuiTree` | 23 | 82 | |
| `GuiGridView` | 42 | 94 | **in active use by `sapgui.mcp`** |
| `GuiSession` | 11 | 44 | |
| `GuiTextedit` | 10 | 29 | |
| `GuiTextField` | 7 | 23 | |
| `GuiTableControl` | 8 | 21 | |
| `GuiComboBox` | 4 | 18 | includes `Key` — see #88 |
| `GuiCheckBox` | 5 | 17 | |
| `GuiRadioButton` | 3 | 14 | |
| `GuiStatusbar` | 1 | 9 | includes the message fields — see #90 |

`GuiCTextField` and `GuiPasswordField` are absent from the table, and the reason turned out not to be the one first given. The original draft said their rows were "the `GuiTextField` row restated". They are not — the tool reports:

```
GuiTextField                23     7   16  ISapTextFieldTarget
GuiCTextField               20     6   14  ISapCTextField *
GuiPasswordField            20     6   14  ISapPasswordField *
```

**Observed, from that run:** the three classes resolved to differently-named interfaces — one `*Target`, two bare — and a subclass reports fewer members than its parent. Those rows are therefore not comparable with each other, whatever the cause.

**The cause, checked against the live library.** `interface_candidates()` tries `ISap<Name>Target` first and falls through to the bare `ISap<Name>`. That the subclasses have no usable `*Target` variant is not inferred from the report — `test_why_a_row_falls_back_to_the_bare_interface` asserts it against the type library itself: for every class the report marks, the first-choice candidate must be absent or carry no members, and `GuiCTextField` and `GuiPasswordField` must actually be in that set. It passes against the installation this snapshot was taken from (`SAPFEWSELib`, 161 type infos).

If a future release gives either class a populated `ISap<Name>Target`, that test fails and names it, and this paragraph is then wrong rather than quietly stale.

The numbers are therefore not so much wrong as **not comparable**, and the "restated row" claim was wrong for a reason that had nothing to do with the guide. The two classes stay out of the table, now for the right reason: including them invites exactly the parent-versus-subclass reading that does not hold.

`diff_typelib.py` now marks any fallback row with `*` and prints what the mark means, because nothing in the output previously distinguished a first-choice resolution from a fallback. Read a marked row only against other marked rows.

`GuiGridView` has the largest gap among classes in active consumer use, but it is not alone: `GuiSession`, `GuiTextField`, `GuiComboBox` and `GuiStatusbar` are all in this table and all four are exercised by the reconstruction in `docs/spike/`. #88 and #90 are gaps in two of them.

## Classes not defined at all

With own members, so defining them would add reach:

`GuiApoGrid` (42), `GuiUtils` (17), `GuiOfficeIntegration` (8), `GuiBarChart` (7), `GuiNetChart` (5), `GuiEAIViewer2D` (4), `GuiStage` (3).

With **zero** own members, so defining them would add typing and nothing else: `GuiChart`, `GuiEAIViewer3D`, `GuiGraphAdapt`, `GuiMap`.

## Classes wrapping none of their own members

Beyond the five in #41 (`GuiCalendar` 23, `GuiPicture` 11, `GuiInputFieldControl` 9, `GuiSplit` 7, `GuiComboBoxControl` 6), the same pattern holds for `GuiSimpleContainer` (14), `GuiScrollContainer` (6), `GuiBox` (4), `GuiCustomControl` (4), `GuiOkCodeField` (2), `GuiDialogShell` (2) and `GuiSplitterContainer` (2).

## What this document is for

**It is a map, not a backlog.** It says what the API exposes, not what anyone calls. Wrapping everything here would recreate the Scripting API 1:1 in Python, most of it never called — the `GuiNetChart` / `GuiBarChart` / `GuiEAIViewer` tail being the clearest example.

This package has grown demand-driven and should continue to. What the map adds is that when a consumer does hit a wall, the fix is a short wrapper rather than type-library archaeology first.

So: consult it when something is missing, not to decide what to build next.

## Provenance

Findings recorded on #41, #88, #90, #91 and #92. `docs/spike/` holds the recorded journey and reconstruction from the #82 spike that produced several of them.
