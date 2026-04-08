# sapsucker#23 — Fast-path opt-out (0.4.1 design)

**Status:** Design draft, awaiting human sign-off.
**Author:** Claude (Opus 4.6, 1M context) on behalf of @hf-kklein.
**Tracking issue:** [Hochfrequenz/sapsucker#23](https://github.com/Hochfrequenz/sapsucker/issues/23)
**Related:** Hochfrequenz/sapsucker#20 (original fast path), Hochfrequenz/sapsucker#22 (the PR that introduced it), Hochfrequenz/sapwebgui.mcp#660 (the consumer bump that surfaced the regression).

## Goal

Stop the downstream sapwebgui.mcp integration suite from native-crashing mid-run on `STATUS_ACCESS_VIOLATION`, **without forfeiting the 53.3× `dump_tree` speedup** that 0.4.0 introduced for any existing caller who is currently happy with it.

## Hard constraints (from the maintainer)

1. **No regressions.** The integration suite must stop crashing.
2. **No performance losses.** Existing 0.4.0 callers who rely on the fast path must keep getting the same speed (a few milliseconds of overhead is acceptable; double-digit-percent slowdown is not).
3. The fix must be testable in CI without a real SAP machine.
4. Backwards compatible with the 0.4.0 public API. No breaking changes.
5. Single sapsucker patch release (0.4.1). The user publishes the release; we do not.

## Definition of Done

This PR is mergeable when ALL of the following hold:

1. **Exact public surface:**
   - Env var name is exactly `SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH` (no abbreviations, no aliases)
   - The kwarg on `GuiVContainer.dump_tree` is exactly `use_fast_path` (keyword-only, default `None`)
   - The kwarg accepts exactly three values: `None` (respect module flag), `True` (force fast path), `False` (force slow path)
2. **Behavioral parity with 0.4.0 in the no-env-var, no-kwarg case:**
   - With env var unset and `dump_tree()` called without `use_fast_path`, the existing 0.4.0 fast path runs unchanged
   - All existing tests in `unittests/test_get_object_tree.py` and `unittests/test_base.py` pass without modification (except for the addition of the autouse fixture documented in the testing section)
3. **All 13 new tests pass** (numbered list in the testing section)
4. **Log line format is preserved:** the `logger.info("dump_tree", extra={...})` call keeps its existing field set (`duration_ms`, `elements`, `depth_reached`, `max_depth_param`, `container_id`, `path`). No new fields, no removed fields, no level change
5. **Type hints + linting pass:** `mypy --strict` clean, `pylint` 10.00/10, `black --check` and `isort --check` clean
6. **No new files created** in `src/sapsucker/` (the change is contained in the existing `components/base.py`)
7. **The existing module-level docstring at `base.py:336-354`** has been edited in place to document the widened semantics — not replaced, not deleted
8. **Manual real-SAP regression check** described in the testing section has been run by the implementer and the integration suite goes green with the env var set on a fresh `sapwebgui.mcp` checkout that points at this branch
9. **PR commit message and release notes use `Refs #23`, NOT `Closes #23`** (see "Issue closure semantics" section)
10. **CI is fully green** on the PR before handoff to the maintainer for merge

## Background

`GuiVContainer.dump_tree` in 0.4.0 calls `GuiSession.GetObjectTree(element_id, props)` — a single COM call that returns JSON for an entire subtree. ~10 s → ~200 ms on a real BP person create screen (276 elements). The win is real and measured.

`GetObjectTree` has a documented native crash bug — [SAP Note 3674808](https://userapps.support.sap.com/sap/support/notes/3674808). On the downstream sapwebgui.mcp full integration test suite (hundreds of tests against a real SAP backend), the test process **reproducibly native-crashes** mid-suite with `STATUS_ACCESS_VIOLATION` (exit code `3221225477`, `0xC0000005`). Both retry runs crashed at the same point, mid-`dump_tree` call inside `test_bp_get_dropdown_options_anrede`. The crash is **not reproducible in isolation** (the same test alone passes; the entire `test_bp_integration.py` file alone passes 10/10) — it requires ~30+ tests of accumulated COM/SAP state to manifest.

A native segfault inside `sapgui.exe` cannot be caught by Python `try/except`. The PR description for 0.4.0 claimed "per-call try/except fallback" as the mitigation; that mitigation only works for Python exceptions, not for native crashes. The process dies before control returns to Python.

## Approach considered and rejected

Four candidate mitigations were evaluated by an independent expert agent (full reasoning lives in the conversation transcript and the GitHub issue):

| Approach | Why rejected |
|---|---|
| **A2. Long-lived worker subprocess** with a COM-attached child via the Running Object Table | Sapsucker's `login.py` is not a pure function — it launches `saplogon.exe`, opens connections, dismisses popups, waits on dynpro state. Re-doing it in a child process is out, so the worker would have to ROT-attach to the parent's logged-in session. That works only if both processes share the same desktop session, which is fragile under service-account deployments. Stacking a subprocess IPC layer *under* sapwebgui.mcp's existing dedicated COM thread doubles the failure surface (two retry layers, two RPC backoff queues fighting each other). ~300 lines of new code with significant accidental complexity for a one-day fix. |
| **C. Dynamic detect-and-skip** — bisect a precondition for the crash and skip the fast path on matching screens | The crash needs ~30+ tests of accumulated COM state, not on-screen state. Nothing sapsucker can read via a cheap pre-check will observe the precondition. Bisecting is hours per hypothesis with no upper bound and no guarantee a clean rule exists. |
| **D. File a SAP support ticket** alone | SAP Note 3674808 already exists; SAP knows; the bug is still there. D should still happen *in parallel* with the code fix, but it does not stop the integration suite from crashing today. |
| **B (default off, opt-in)** — the expert's first-pass recommendation | Conflicts with the maintainer's hard constraint #2: defaulting off would silently slow every existing 0.4.0 user from 200 ms back to 10 s. Not acceptable. |

## Chosen approach: B with default ON, opt-out via env var + per-call kwarg

The fast path stays **enabled by default** (zero perf regression for current 0.4.0 users). A new env var and a new per-call kwarg let any caller disable the fast path explicitly. The downstream sapwebgui.mcp test environment opts out via the env var in `tox.ini`, which stops the integration suite from crashing — without affecting production sapwebgui.mcp users, who keep the 50× speedup.

This is opinionated: we are choosing perf wins for the median user over crash safety for the worst-case test environment. The maintainer made that call explicitly. The escape hatches are present and easy to flip when SAP eventually fixes Note 3674808.

## Architecture

One file changes in sapsucker: `src/sapsucker/components/base.py`. No new modules, no new IPC, no new caches. We repurpose the existing `_fast_path_permanently_disabled` module flag and the existing `_reset_fast_path_cache` helper that were introduced in 0.4.0 (PR #22, reviewer finding I4) for the AttributeError-on-legacy-SAP path. Their semantics widen from "permanent failure cache" to "is the fast path enabled at all", but the existing AttributeError path keeps working unchanged.

## Components

### 1. New module-level constant

```python
_SAPSUCKER_DISABLE_FAST_PATH_ENV = "SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH"
```

Naming chosen for symmetry with the existing `_fast_path_permanently_disabled` flag and to make `=1` mean "disabled" (which matches what users will write in their `.env` file or `tox.ini`).

### 2. New module-level helper

```python
def _read_fast_path_disabled_from_env() -> bool:
    """Return True if the env var disables the fast path, False otherwise.

    Truthy values that disable the fast path: "1", "true", "yes", "on"
    (case-insensitive). Anything else (unset, "0", "false", empty,
    garbage) leaves the fast path enabled.
    """
```

The truthy-string parser is intentionally a small allowlist, not a permissive cast, so a typo like `SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH=tru` does NOT silently disable the fast path. The cost of a false negative (typo, fast path stays on) is one test crash. The cost of a false positive (typo, fast path silently disabled) is a 50× perf regression that the user can't see in any log line — strictly worse.

### 3. Repurposed module flag (renamed in spirit, kept in name)

```python
_fast_path_permanently_disabled: bool = _read_fast_path_disabled_from_env()
```

The variable name stays the same to minimize diff churn, preserve git blame, and avoid touching the existing 37 unit tests that reference it. Its semantics widen: it now reflects EITHER "the env var disabled this at startup" OR "an AttributeError disabled this at runtime" OR "this initial-state default plus any subsequent runtime override".

The existing module-level docstring block at `base.py:336-354` (the one starting "Reviewer I4 on PR #22") **must be updated, not replaced**. The new text should add a paragraph noting that the initial value is now `_read_fast_path_disabled_from_env()` and explaining what the env var does, while preserving the existing prose about reviewer I4, the AttributeError-on-legacy-SAP path, and the process-global trade-off. The implementer should treat this as an edit-in-place, not a rewrite.

**Module-import-time read contract:** The default value is read **once at module import time**, before any test's `monkeypatch.setenv` can run. Tests that mutate the env var MUST also call `_reset_fast_path_cache()` afterwards to re-read it. This is a non-negotiable contract — without the reset call, monkeypatch is ineffective. The defensive `autouse` fixture in the testing section enforces this for the existing test file.

### 4. Updated `_reset_fast_path_cache()`

```python
def _reset_fast_path_cache() -> None:
    """Reset the fast-path-disabled cache. Used by unit tests; not public API."""
    global _fast_path_permanently_disabled
    _fast_path_permanently_disabled = _read_fast_path_disabled_from_env()
```

Re-reads the env var instead of unconditionally setting `False`. This lets tests do:

```python
def test_env_var_disables(monkeypatch):
    monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", "1")
    _reset_fast_path_cache()
    # ... call dump_tree on a mock, assert slow path was taken
```

The `monkeypatch.setenv` is automatically reverted by pytest at test teardown.

### 5. New keyword-only kwarg on `GuiVContainer.dump_tree`

```python
def dump_tree(
    self,
    max_depth: int | None = None,
    *,
    use_fast_path: bool | None = None,
) -> list[ElementInfo]:
```

- `use_fast_path=None` (default) → respect the module flag (current behavior)
- `use_fast_path=True` → force the fast path attempt for this call only, even if the module flag says it's disabled
- `use_fast_path=False` → force the slow path for this call only, even if the module flag says it's enabled

The kwarg is keyword-only (after `*`) for two reasons: (a) it forbids accidental positional usage that could collide with future positional args; (b) it makes the call site self-documenting (`dump_tree(use_fast_path=False)` is clear at the call site). The default `None` is critical: it preserves perfect backwards compatibility with every 0.4.0 caller that passes `dump_tree()` or `dump_tree(max_depth=N)`.

The kwarg does NOT mutate `_fast_path_permanently_disabled`. It is a per-call override only. Calling `dump_tree(use_fast_path=False)` once does not affect any subsequent call.

### Updated `dump_tree` decision logic

```python
def dump_tree(
    self,
    max_depth: int | None = None,
    *,
    use_fast_path: bool | None = None,
) -> list[ElementInfo]:
    global _fast_path_permanently_disabled  # noqa: PLW0603  pylint: disable=global-statement

    # ... existing setup (start time, container_id read) ...

    # Decide which path to take.
    # Precedence: per-call kwarg > module flag.
    if use_fast_path is False:
        fast_path_attempt = False
    elif use_fast_path is True:
        fast_path_attempt = True
    else:
        fast_path_attempt = not _fast_path_permanently_disabled

    if not fast_path_attempt or container_id == "<unknown>":
        result = _dump_tree_recursive(self._com, 0, effective_depth_cap)
        path = "slow"
    else:
        try:
            result = _dump_tree_via_get_object_tree(self._com, container_id, effective_depth_cap)
            path = "fast"
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if _is_permanent_fast_path_failure(exc):
                _fast_path_permanently_disabled = True
                logger.warning(...)
            else:
                logger.debug(...)
            result = _dump_tree_recursive(self._com, 0, effective_depth_cap)
            path = "slow"
```

**`global` statement placement:** Keep the `global _fast_path_permanently_disabled` declaration at the **top of the function body**, exactly where it lives in the current 0.4.0 code (line 527). Do NOT move it inside the except branch. The current placement is correct because it must precede any read or write of the module variable in the function, and we read it in the `else` branch of the precedence decision.

The existing `_is_permanent_fast_path_failure` AttributeError path still runs and still mutates the module flag. If a caller forces `use_fast_path=True` on a legacy SAP system, the AttributeError disables the module flag (consistent with current 0.4.0 behavior). The forced kwarg only overrides the *initial decision*, not the runtime fallback.

**Log line format preservation:** The `logger.info("dump_tree", extra={...})` call at the end of the function MUST keep its exact existing field set: `duration_ms`, `elements`, `depth_reached`, `max_depth_param`, `container_id`, `path`. No new fields, no removed fields, no level change. The existing tests assert on the `path` value being `"fast"` or `"slow"`; downstream consumers (sapwebgui.mcp) parse the line by field name. Adding a field would be silently safe; removing or renaming one would break consumers.

## Data flow

```
caller: dump_tree(max_depth=200, use_fast_path=None)
   │
   ├── read container_id (existing)
   │
   ├── decide fast_path_attempt:
   │     - kwarg=False → False
   │     - kwarg=True  → True
   │     - kwarg=None  → not _fast_path_permanently_disabled
   │
   ├── if not fast_path_attempt OR container_id == "<unknown>":
   │     └─ slow path
   │
   └── else:
         try _dump_tree_via_get_object_tree:
            ├─ success → fast path
            └─ Python exception:
                  ├─ if _is_permanent_fast_path_failure → set module flag, log WARNING, slow path
                  └─ else                                → log DEBUG, slow path

         (native segfault here kills the process — not catchable)
```

## Error handling

Unchanged from 0.4.0 behavior. The existing `_is_permanent_fast_path_failure` heuristic and the try/except around `_dump_tree_via_get_object_tree` keep working. This design does not attempt to catch native segfaults — it stops attempting the fast path *only if explicitly told*, so the segfault never gets a chance to fire in environments that disable it.

## Testing

All COM-free unit tests, no real SAP needed. Plays into the existing `unittests/test_get_object_tree.py` patterns (the `TestDumpTreeFastPath` class already has the `_reset_fast_path_cache` machinery and a mocked COM hierarchy).

### New tests (this PR adds)

1. `test_env_var_unset_keeps_fast_path_enabled` — `monkeypatch.delenv(...)`, `_reset_fast_path_cache()`, mock COM where `GetObjectTree` would succeed, assert fast path was taken (`path="fast"` in log, `GetObjectTree` was called)
2. `test_env_var_set_to_1_disables_fast_path` — `monkeypatch.setenv(..., "1")`, reset, assert slow path was taken (`path="slow"`, `GetObjectTree` NOT called)
3. Parametrize test 2 over `["1", "true", "True", "yes", "ON", "TrUe"]` — case-insensitive truthy values
4. Parametrize a `..._does_not_disable` test over `["", "0", "false", "no", "off", "garbage", "tru", "yeah"]` — anything other than the allowlist leaves it enabled
5. `test_kwarg_use_fast_path_false_overrides_enabled_default` — env unset, kwarg `False`, assert slow path taken
6. `test_kwarg_use_fast_path_true_overrides_disabled_default` — env `"1"`, kwarg `True`, assert fast path taken
7. `test_kwarg_does_not_mutate_module_flag` — env unset, call once with `use_fast_path=False`, then call once with default — second call must still take fast path; assert `_fast_path_permanently_disabled` is still `False` after both
8. `test_kwarg_none_respects_module_flag_when_disabled_by_env` — env `"1"`, kwarg `None`, assert slow path
9. `test_kwarg_none_respects_module_flag_when_disabled_by_attribute_error` — env unset, simulate `AttributeError` from `GetObjectTree` to flip module flag, then call with kwarg `None`, assert slow path on the second call
10. `test_existing_attribute_error_disable_still_works_with_forced_kwarg` — env unset, kwarg `True`, fast path raises `AttributeError`, assert slow path on the same call AND module flag is now `True`
11. `test_truthy_env_check_is_case_insensitive_helper_unit_test` — direct unit test of `_read_fast_path_disabled_from_env` for the truthy/falsy matrix without going through dump_tree
12. `test_kwarg_use_fast_path_is_keyword_only` — `dump_tree(200, False)` raises `TypeError`. Pins the keyword-only enforcement that the spec calls out as a deliberate design choice.
13. `test_kwarg_true_with_runtime_attribute_error_flag_still_takes_slow_path_on_second_call` — env unset, **first call** raises `AttributeError` (which sets `_fast_path_permanently_disabled = True` at runtime), then **second call** with `use_fast_path=True` re-attempts the fast path, raises `AttributeError` again, falls back to slow. Distinct from test 6 because the flag is set at runtime, not at import time, and distinct from test 10 because it covers what happens on the second call after the runtime flip.

### Existing test sweep

The existing 37 tests in `test_get_object_tree.py` and `test_base.py` must keep passing without modification. The existing `TestDumpTreeFastPath` class and the `test_fast_path_permanently_disabled_on_attribute_error` / `test_transient_failures_do_NOT_disable_fast_path` tests all rely on `_fast_path_permanently_disabled` being `False` after `_reset_fast_path_cache()` is called. After this change, that's only true when the env var is unset.

**Decision:** add an `autouse=True` fixture **preemptively** at the top of `test_get_object_tree.py` that does:

```python
@pytest.fixture(autouse=True)
def _reset_fast_path_state(monkeypatch):
    """Ensure each test starts with the env var unset and the module flag freshly read.

    Without this, a developer running the suite with
    SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH set in their shell would see
    mysterious failures in the existing TestDumpTreeFastPath suite. The
    fixture also clears the cache after the test so subsequent files
    don't inherit transient state from a runtime AttributeError flip.
    """
    monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
    from sapsucker.components import base as _base_module
    _base_module._reset_fast_path_cache()
    yield
    _base_module._reset_fast_path_cache()
```

This is a defensive add — the spec reviewer flagged that the original "no preemptive fixture" stance was fragile, and they're right: a developer running tests with the env var exported in their shell would see two existing tests fail mysteriously. The fixture costs nothing and removes that footgun.

Tests that explicitly set the env var (the new tests #2-#13) call `monkeypatch.setenv` AFTER the autouse fixture runs, then call `_reset_fast_path_cache()` themselves to make their setting active.

We will still verify in step 0 of the implementation that the existing tests pass with the autouse fixture in place — running the full unit suite before touching any production code is a freebie that catches integration issues early.

### Real SAP regression check (NOT in CI)

The implementer should ALSO bump the local sapwebgui.mcp checkout to point at this branch, set `SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH=1` in `tox.ini`'s `[testenv:integration_tests]`, and run the full integration suite once to confirm the crash no longer happens. This is a manual gate, not a CI gate. If the suite goes green, we know the bump-plus-flag combination works end-to-end. This validation happens BEFORE the PR is merged but AFTER the unit tests are green.

## Version + release notes

- Version is `dynamic` from VCS tags in `pyproject.toml`. Bumping is a `git tag v0.4.1` operation that the maintainer does at release time, not us.
- Release notes draft (the maintainer publishes the release; we provide the draft):
  > **Behavior:** `dump_tree`'s `GuiSession.GetObjectTree` fast path remains **enabled by default** — no perf regression vs 0.4.0 for any existing caller. New escape hatch for environments hitting [SAP Note 3674808](https://userapps.support.sap.com/sap/support/notes/3674808): set `SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH=1` to globally force the slow path, or pass `dump_tree(use_fast_path=False)` for a single call. Refs #23.

## Issue closure semantics

**Important:** the PR commit message and the release notes for 0.4.1 use `Refs #23`, NOT `Closes #23`. Issue #23 stays open after this PR merges, until the downstream sapwebgui.mcp follow-up PR (which sets the env var in `tox.ini` and bumps to 0.4.1) merges and the integration suite is verified green.

The reasoning: the maintainer's hard constraint #1 is "the integration suite must stop crashing". This PR makes the *escape hatch available*; it does not by itself stop the integration suite from crashing. Auto-closing #23 on merge of this PR would mark the issue as resolved before its actual acceptance criterion is met. The follow-up consumer-side PR is the one that closes #23.

The handoff: after this PR merges and the maintainer publishes 0.4.1 to PyPI, the next task will (a) bump sapwebgui.mcp to `sapsucker==0.4.1`, (b) add `setenv = SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH=1` to the `[testenv:integration_tests]` block in `tox.ini`, (c) re-run the full integration suite to confirm green, (d) open a PR with `Closes Hochfrequenz/sapsucker#23` and `Closes #649` in the body. That PR is the one that actually resolves both issues.

## Out of scope

- **Subprocess worker** (rejected, see "Approach considered and rejected" above).
- **Crash heuristics** (rejected, no clean precondition expected).
- **Filing the SAP support ticket** — separate task, the maintainer can do it whenever, not blocking this PR.
- **CHANGELOG file** — sapsucker doesn't have one; release notes go on the GitHub Release.
- **The downstream sapwebgui.mcp follow-up bump to 0.4.1 + tox.ini env var** — separate PR, opened after this one is merged and 0.4.1 is published to PyPI.

## Open questions

None. All design questions were resolved by the maintainer's "no perf regression" constraint and the expert recommendation, with the explicit policy override on the default (on, not off).

## Failure modes

1. **The crash isn't actually `GetObjectTree`.** Strong evidence says it is (mid-`dump_tree` log, Note 3674808 matches, 0.3.1 didn't have it, segfault not Python-catchable, reproducible at the same point twice in a row). But we never bisected to the specific COM call. If the integration suite still crashes after sapwebgui.mcp adopts 0.4.1 with the env var set, the bug is elsewhere (stale COM proxies in `_com_thread.py`, RPC handling, sapgui.exe memory leak unrelated to GetObjectTree). The cost of being wrong is one wasted release cycle plus a fresh debugging session — acceptable, with a clear falsification signal.
2. **A user accidentally sets the env var to a typo and silently slows their workload by 50×.** Mitigated by the small allowlist parser (typos do NOT match the allowlist, so they leave the fast path enabled). The `dump_tree` perf log line includes `path="fast"|"slow"` so any production monitoring already has visibility into the actual path taken.
3. **A future SAP version makes the segfault catchable as a Python exception.** In that future, the existing `_is_permanent_fast_path_failure` heuristic would handle it transparently and this fix becomes a no-op safety net. Cost: zero.
4. **A user explicitly sets `use_fast_path=True` on every call** to opt back into the speed even after disabling globally. This is intended behavior — power users get an escape hatch in both directions. The kwarg is documented as "I know what I'm doing".

## What this fix does NOT do

- It does not eliminate the underlying `sapgui.exe` bug.
- It does not make the fast path safe under sustained-load workloads when enabled.
- It does not split-brain protect against environments that have the env var set in some processes but not others.

All three are acceptable. The bar the maintainer set is "stop the integration suite from native-crashing without losing perf", and the design hits that bar exactly. The deeper fix lives in `sapgui.exe` and is gated on SAP fixing Note 3674808, which is out of our control.
