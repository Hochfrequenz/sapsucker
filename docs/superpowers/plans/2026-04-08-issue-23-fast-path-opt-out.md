# sapsucker#23 — Fast-path opt-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-out mechanism (env var + per-call kwarg) for the `dump_tree` `GuiSession.GetObjectTree` fast path introduced in 0.4.0, so environments hitting [SAP Note 3674808](https://userapps.support.sap.com/sap/support/notes/3674808) (the documented native crash bug) can force the slow path. The fast path stays **enabled by default** to preserve the 0.4.0 perf win for every existing caller.

**Architecture:** Single-file change in `src/sapsucker/components/base.py`. New helper `_read_fast_path_disabled_from_env()` reads `SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH` at module import time. The existing `_fast_path_permanently_disabled` module flag's initial value becomes env-driven (False unless the env var is truthy). The existing `_reset_fast_path_cache()` re-reads the env var. New keyword-only kwarg `dump_tree(use_fast_path: bool | None = None)` overrides the module flag per-call. The existing AttributeError-based fallback machinery is unchanged.

**Tech Stack:** Python 3.11+, pydantic v2 (existing), pytest (existing), unittest.mock (existing). No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-04-08-issue-23-fast-path-opt-out-design.md`](../specs/2026-04-08-issue-23-fast-path-opt-out-design.md)

---

## File structure

| File | Status | Responsibility |
|---|---|---|
| `src/sapsucker/components/base.py` | Modify | Add helper `_read_fast_path_disabled_from_env`, change `_fast_path_permanently_disabled` initial value, update `_reset_fast_path_cache`, add `use_fast_path` kwarg + precedence logic to `GuiVContainer.dump_tree`, edit existing module-level comment in place |
| `unittests/test_get_object_tree.py` | Modify | Add autouse fixture `_reset_fast_path_state`, add 13 new tests covering env var, kwarg, precedence, keyword-only enforcement, runtime AttributeError interaction |
| `docs/superpowers/specs/2026-04-08-issue-23-fast-path-opt-out-design.md` | (Already exists) | Reference only — read this before starting |

No new files. No deletions.

---

## Task 1: Baseline + helper + autouse fixture (infrastructure, no behavior change)

**Files:**
- Modify: `C:/github/sapsucker/src/sapsucker/components/base.py:333` (insert helper above `_SESSION_TYPE_NAME`)
- Modify: `C:/github/sapsucker/unittests/test_get_object_tree.py:46` (insert autouse fixture above the `FIXTURES = ...` line)
- Test: `C:/github/sapsucker/unittests/test_get_object_tree.py` (add new test class `TestReadFastPathDisabledFromEnv`)

### Steps

- [ ] **Step 1.1: Run baseline unit suite to confirm starting state is green**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/test_get_object_tree.py unittests/test_base.py -v
```

Expected: all tests pass. If any fail BEFORE we touch anything, stop and report — the plan assumes a green starting state.

- [ ] **Step 1.2: Add the helper function to `base.py`**

Insert this block at `src/sapsucker/components/base.py`, immediately above the existing `_SESSION_TYPE_NAME = "GuiSession"` line (currently line 333):

```python
_SAPSUCKER_DISABLE_FAST_PATH_ENV = "SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH"
_FAST_PATH_DISABLE_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def _read_fast_path_disabled_from_env() -> bool:
    """Return True if the env var disables the fast path, False otherwise.

    Truthy values that disable the fast path: ``"1"``, ``"true"``, ``"yes"``,
    ``"on"`` (case-insensitive). Anything else — unset, ``"0"``, ``"false"``,
    empty string, garbage, typos — leaves the fast path enabled.

    The allowlist is intentionally small. The cost of a false negative
    (typo, fast path stays on, user crashes) is one test crash. The cost
    of a false positive (typo, fast path silently disabled) is a 50× perf
    regression that doesn't show up in any error log. Strictly worse —
    so we err on the side of staying fast.
    """
    raw = os.environ.get(_SAPSUCKER_DISABLE_FAST_PATH_ENV, "")
    return raw.strip().lower() in _FAST_PATH_DISABLE_TRUTHY_VALUES
```

Verify `import os` is already at the top of the file. (It should be — sapsucker uses os elsewhere. Grep to confirm: `grep -n "^import os" src/sapsucker/components/base.py`.) If it's not present, add `import os` to the top alongside the existing imports.

- [ ] **Step 1.3: Write the unit test for the helper**

Insert this block at the end of `unittests/test_get_object_tree.py`, after the existing `TestDumpTreeFastPath` class but before the file ends:

```python
class TestReadFastPathDisabledFromEnv:
    """Pin the env-var allowlist parser. The exact set of truthy values
    is the contract that the spec at docs/superpowers/specs/
    2026-04-08-issue-23-fast-path-opt-out-design.md commits to.
    """

    @pytest.mark.parametrize(
        "value",
        ["1", "true", "True", "TRUE", "TrUe", "yes", "YES", "on", "ON"],
    )
    def test_truthy_values_disable_fast_path(self, monkeypatch, value):
        monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", value)
        assert _base_module._read_fast_path_disabled_from_env() is True

    @pytest.mark.parametrize(
        "value",
        ["", "0", "false", "False", "FALSE", "no", "off", "garbage", "tru", "yeah", "2"],
    )
    def test_falsy_or_garbage_values_keep_fast_path_enabled(self, monkeypatch, value):
        monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", value)
        assert _base_module._read_fast_path_disabled_from_env() is False

    def test_unset_keeps_fast_path_enabled(self, monkeypatch):
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        assert _base_module._read_fast_path_disabled_from_env() is False
```

- [ ] **Step 1.4: Run the helper test to verify it passes**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/test_get_object_tree.py::TestReadFastPathDisabledFromEnv -v
```

Expected: 21 tests pass (9 truthy + 11 falsy + 1 unset = 21).

- [ ] **Step 1.5: Add the autouse fixture to the test module**

Insert this block at `unittests/test_get_object_tree.py`, immediately above the existing `# ---` "Fixture path" section (currently around line 46):

```python
# ---------------------------------------------------------------------------
# Autouse fixture: env-var isolation for the fast-path-disable feature
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_fast_path_state(monkeypatch):
    """Ensure each test starts with the env var unset and the module flag
    freshly read.

    Without this, a developer running the suite with
    SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH set in their shell would see
    mysterious failures in the existing TestDumpTreeFastPath suite (the
    AttributeError-and-cache tests assume the module flag is False at
    test start). The fixture also clears the cache after the test so
    subsequent tests don't inherit transient state from a runtime
    AttributeError flip.
    """
    monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
    _base_module._reset_fast_path_cache()
    yield
    _base_module._reset_fast_path_cache()
```

- [ ] **Step 1.6: Run the full `test_get_object_tree.py` to verify the autouse fixture doesn't break any existing test**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/test_get_object_tree.py -v
```

Expected: all existing tests still pass + the 21 new helper tests = 58 tests total. If anything fails, stop and investigate — the fixture should be additive only.

- [ ] **Step 1.7: Run the full `test_base.py` too, to verify no cross-file pollution**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/test_base.py unittests/test_get_object_tree.py -v
```

Expected: all tests pass.

- [ ] **Step 1.8: Commit**

```bash
cd C:/github/sapsucker && git add src/sapsucker/components/base.py unittests/test_get_object_tree.py && git commit -m "$(cat <<'EOF'
test(base): add fast-path env-var helper and autouse isolation fixture

Pure infrastructure for sapsucker#23. New helper
_read_fast_path_disabled_from_env() reads
SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH and returns True only for
the small allowlist {1, true, yes, on} (case-insensitive). New
autouse fixture in test_get_object_tree.py clears the env var and
resets the module-level fast-path cache before and after every test
so a developer running the suite with the env var exported in their
shell does not see mysterious failures in the existing
TestDumpTreeFastPath tests.

No behavior change yet; the helper is not wired into anything in
this commit.

Refs Hochfrequenz/sapsucker#23.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire env var into module flag init + dump_tree precedence + kwarg

**Files:**
- Modify: `C:/github/sapsucker/src/sapsucker/components/base.py:336-355` (update existing comment block in place)
- Modify: `C:/github/sapsucker/src/sapsucker/components/base.py:355` (change initial value of `_fast_path_permanently_disabled`)
- Modify: `C:/github/sapsucker/src/sapsucker/components/base.py:358-361` (`_reset_fast_path_cache` re-reads env var)
- Modify: `C:/github/sapsucker/src/sapsucker/components/base.py:494-583` (`dump_tree` signature + precedence logic)
- Test: `C:/github/sapsucker/unittests/test_get_object_tree.py` (add new test class `TestDumpTreeFastPathOptOut`)

### Steps

- [ ] **Step 2.1: Write the failing tests for env-var-driven default**

Insert this new test class at the end of `unittests/test_get_object_tree.py`, after `TestReadFastPathDisabledFromEnv`:

```python
class TestDumpTreeFastPathOptOut:
    """Tests for the env var + per-call kwarg opt-out machinery
    introduced in 0.4.1 to fix sapsucker#23 (SAP Note 3674808 native
    crash). The fast path is enabled by default; this class verifies
    every escape hatch.
    """

    def _make_session_and_vc(self, json_response: str | None = None, raises: BaseException | None = None):
        """Helper: build a (session_mock, GuiVContainer) pair where
        GetObjectTree either returns the given JSON or raises the
        given exception. Both branches share the same wnd[0] mock
        hierarchy so test bodies focus on the path-decision logic.
        """
        if json_response is not None and raises is not None:
            raise ValueError("pass json_response OR raises, not both")
        session_mock = MagicMock(name="session_com")
        session_mock.Type = "GuiSession"
        session_mock.Parent = None
        if raises is not None:
            session_mock.GetObjectTree = MagicMock(side_effect=raises)
        else:
            session_mock.GetObjectTree = MagicMock(return_value=json_response or "{}")

        # Build a 1-child tree so the slow path returns something verifiable.
        child = make_mock_com(type_as_number=31, id="c1", name="txtA")
        parent = make_mock_com(
            container_type=True,
            id="/app/con[0]/ses[0]/wnd[0]",
            children=[child],
        )
        _wire_parent_to_session(parent, session_mock)
        return session_mock, GuiVContainer(parent)

    # ----- Env-var-driven default -----

    def test_env_var_unset_keeps_fast_path_enabled(self, caplog, monkeypatch):
        """Spec test #1: with the env var unset, the fast path runs."""
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        _base_module._reset_fast_path_cache()

        json_response = BP_FULL.read_text(encoding="utf-8")
        session_mock, vc = self._make_session_and_vc(json_response=json_response)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree()

        session_mock.GetObjectTree.assert_called_once()
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "fast"

    def test_env_var_set_to_1_disables_fast_path(self, caplog, monkeypatch):
        """Spec test #2: env var = "1" forces the slow path globally."""
        monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", "1")
        _base_module._reset_fast_path_cache()

        session_mock, vc = self._make_session_and_vc(json_response="{}")

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree()

        session_mock.GetObjectTree.assert_not_called()
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "slow"

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "ON", "TrUe"])
    def test_env_var_truthy_values_disable_fast_path(self, caplog, monkeypatch, value):
        """Spec test #3: case-insensitive allowlist."""
        monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", value)
        _base_module._reset_fast_path_cache()

        session_mock, vc = self._make_session_and_vc(json_response="{}")

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree()

        session_mock.GetObjectTree.assert_not_called()
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "slow"

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "garbage", "tru", "yeah"])
    def test_env_var_falsy_or_garbage_does_not_disable_fast_path(self, caplog, monkeypatch, value):
        """Spec test #4: anything outside the allowlist leaves fast path on."""
        monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", value)
        _base_module._reset_fast_path_cache()

        json_response = BP_FULL.read_text(encoding="utf-8")
        session_mock, vc = self._make_session_and_vc(json_response=json_response)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree()

        session_mock.GetObjectTree.assert_called_once()
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "fast"

    # ----- Per-call kwarg overrides -----

    def test_kwarg_use_fast_path_false_overrides_enabled_default(self, caplog, monkeypatch):
        """Spec test #5: kwarg=False forces slow path even when default is fast."""
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        _base_module._reset_fast_path_cache()

        session_mock, vc = self._make_session_and_vc(json_response="{}")

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree(use_fast_path=False)

        session_mock.GetObjectTree.assert_not_called()
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "slow"

    def test_kwarg_use_fast_path_true_overrides_disabled_default(self, caplog, monkeypatch):
        """Spec test #6: kwarg=True forces fast path even when env var disables."""
        monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", "1")
        _base_module._reset_fast_path_cache()

        json_response = BP_FULL.read_text(encoding="utf-8")
        session_mock, vc = self._make_session_and_vc(json_response=json_response)

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree(use_fast_path=True)

        session_mock.GetObjectTree.assert_called_once()
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "fast"

    def test_kwarg_does_not_mutate_module_flag(self, caplog, monkeypatch):
        """Spec test #7: per-call kwarg is per-call only — does NOT touch
        the module flag. Subsequent calls with default kwarg should see
        the original module-flag value, not the value the kwarg implied.
        """
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        _base_module._reset_fast_path_cache()

        json_response = BP_FULL.read_text(encoding="utf-8")
        session_mock, vc = self._make_session_and_vc(json_response=json_response)

        # First call with kwarg=False → slow path
        vc.dump_tree(use_fast_path=False)
        session_mock.GetObjectTree.assert_not_called()
        assert _base_module._fast_path_permanently_disabled is False

        # Second call with default kwarg → fast path again (module flag unchanged)
        vc.dump_tree()
        session_mock.GetObjectTree.assert_called_once()
        assert _base_module._fast_path_permanently_disabled is False

    def test_kwarg_none_respects_module_flag_when_disabled_by_env(self, caplog, monkeypatch):
        """Spec test #8: kwarg=None (the default) honors the env-driven flag."""
        monkeypatch.setenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", "1")
        _base_module._reset_fast_path_cache()

        session_mock, vc = self._make_session_and_vc(json_response="{}")

        with caplog.at_level(logging.INFO, logger="sapsucker.components.base"):
            vc.dump_tree(use_fast_path=None)  # explicit None for clarity

        session_mock.GetObjectTree.assert_not_called()
        rec = next(r for r in caplog.records if r.message == "dump_tree")
        assert rec.path == "slow"

    # ----- Interaction with the existing AttributeError-based runtime disable -----

    def test_kwarg_none_respects_module_flag_when_disabled_by_attribute_error(self, monkeypatch):
        """Spec test #9: env unset, fast path raises AttributeError on call 1
        which sets the module flag to True at runtime; call 2 with default
        kwarg must take the slow path.
        """
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        _base_module._reset_fast_path_cache()

        session_mock, vc = self._make_session_and_vc(raises=AttributeError("GetObjectTree"))

        # First call: AttributeError, slow path, module flag set
        vc.dump_tree()
        assert _base_module._fast_path_permanently_disabled is True

        # Second call with default kwarg: skips fast path entirely
        session_mock.GetObjectTree.reset_mock()
        vc.dump_tree()
        session_mock.GetObjectTree.assert_not_called()

    def test_kwarg_true_with_attribute_error_still_falls_back(self, monkeypatch):
        """Spec test #10: kwarg=True forces the fast path attempt; if that
        attempt raises AttributeError, the existing fallback still runs
        AND the module flag still gets set (consistent with 0.4.0 behavior).
        """
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        _base_module._reset_fast_path_cache()

        session_mock, vc = self._make_session_and_vc(raises=AttributeError("GetObjectTree"))

        # Single call with kwarg=True: attempts fast path, AttributeError, falls back, sets flag
        vc.dump_tree(use_fast_path=True)
        session_mock.GetObjectTree.assert_called_once()
        assert _base_module._fast_path_permanently_disabled is True

    def test_kwarg_use_fast_path_is_keyword_only(self, monkeypatch):
        """Spec test #12: use_fast_path is keyword-only. Positional usage
        must raise TypeError. Pins the keyword-only design choice.
        """
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        _base_module._reset_fast_path_cache()

        _, vc = self._make_session_and_vc(json_response="{}")

        with pytest.raises(TypeError):
            vc.dump_tree(200, False)  # type: ignore[misc]

    def test_kwarg_true_with_runtime_attribute_error_flag_second_call(self, monkeypatch):
        """Spec test #13: env unset, first call raises AttributeError (sets
        module flag at runtime), second call with kwarg=True re-attempts
        the fast path (raises AttributeError again because the mock is
        still configured to raise), and falls back. The kwarg overrides
        the *initial decision*, not the runtime fallback.
        """
        monkeypatch.delenv("SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH", raising=False)
        _base_module._reset_fast_path_cache()

        session_mock, vc = self._make_session_and_vc(raises=AttributeError("GetObjectTree"))

        # First call: AttributeError, sets flag
        vc.dump_tree()
        assert _base_module._fast_path_permanently_disabled is True
        first_call_count = session_mock.GetObjectTree.call_count
        assert first_call_count == 1

        # Second call with kwarg=True: re-attempts the fast path (overrides flag),
        # AttributeError fires AGAIN, falls back, returns successfully
        vc.dump_tree(use_fast_path=True)
        assert session_mock.GetObjectTree.call_count == 2
```

- [ ] **Step 2.2: Run the new tests to confirm they fail (TDD)**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/test_get_object_tree.py::TestDumpTreeFastPathOptOut -v
```

Expected: most tests FAIL with one of:
- `AttributeError` on `_read_fast_path_disabled_from_env` not being called by `_reset_fast_path_cache` yet (the env-var-driven default tests)
- `TypeError` because `use_fast_path` is not a valid kwarg yet (the kwarg tests)

This is the RED step of red-green-refactor. The next step makes them green.

- [ ] **Step 2.3: Update the existing module-level comment at `base.py:336-354`**

Read the existing comment block first:

```bash
cd C:/github/sapsucker && sed -n '336,355p' src/sapsucker/components/base.py
```

Then edit the comment block in `src/sapsucker/components/base.py` (lines 336–355) to add a new paragraph at the END of the existing prose, just before the `_fast_path_permanently_disabled: bool = False` line. The new paragraph should read:

```
# Initial value (0.4.1+): the env var
# ``SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH`` (allowlist:
# ``1/true/yes/on`` case-insensitive) lets environments hitting SAP Note
# 3674808 force the fast path off at process startup. The flag still
# behaves as a runtime cache for the AttributeError path described
# above; the env var only sets its initial value. Per-call override is
# also available via ``GuiVContainer.dump_tree(use_fast_path=...)``;
# see that method's docstring.
```

Do **not** delete or restructure any existing prose in the comment. Edit in place.

- [ ] **Step 2.4: Change the initial value of `_fast_path_permanently_disabled`**

In `src/sapsucker/components/base.py`, change the existing line:

```python
_fast_path_permanently_disabled: bool = False  # pylint: disable=invalid-name
```

to:

```python
_fast_path_permanently_disabled: bool = _read_fast_path_disabled_from_env()  # pylint: disable=invalid-name
```

- [ ] **Step 2.5: Update `_reset_fast_path_cache` to re-read the env var**

In `src/sapsucker/components/base.py`, change the existing function:

```python
def _reset_fast_path_cache() -> None:
    """Reset the fast-path-disabled cache. Used by unit tests; not public API."""
    global _fast_path_permanently_disabled  # noqa: PLW0603  pylint: disable=global-statement
    _fast_path_permanently_disabled = False
```

to:

```python
def _reset_fast_path_cache() -> None:
    """Reset the fast-path-disabled cache. Used by unit tests; not public API.

    Re-reads ``SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH`` from the
    environment so tests that mutate the env var via
    ``monkeypatch.setenv`` see the new value after calling this.
    """
    global _fast_path_permanently_disabled  # noqa: PLW0603  pylint: disable=global-statement
    _fast_path_permanently_disabled = _read_fast_path_disabled_from_env()
```

- [ ] **Step 2.6: Add the `use_fast_path` kwarg and precedence logic to `GuiVContainer.dump_tree`**

In `src/sapsucker/components/base.py`, locate the existing `dump_tree` method (currently starting at line 494). Change the signature from:

```python
    def dump_tree(self, max_depth: int | None = None) -> list[ElementInfo]:
```

to:

```python
    def dump_tree(
        self,
        max_depth: int | None = None,
        *,
        use_fast_path: bool | None = None,
    ) -> list[ElementInfo]:
```

In the same method body, replace the existing fast-path-decision block. The current block (around lines 542–568) reads:

```python
        # Fast path: bulk-read via GuiSession.GetObjectTree.
        # Skip the attempt entirely if a previous call has already proven
        # the fast path is permanently unsupported on this process's SAP
        # version (e.g. SAP GUI < 7.70 PL3). See
        # ``_fast_path_permanently_disabled``.
        if _fast_path_permanently_disabled or container_id == "<unknown>":
            result = _dump_tree_recursive(self._com, 0, effective_depth_cap)
            path = "slow"
        else:
            try:
                result = _dump_tree_via_get_object_tree(self._com, container_id, effective_depth_cap)
                path = "fast"
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if _is_permanent_fast_path_failure(exc):
                    _fast_path_permanently_disabled = True
                    logger.warning(
                        "dump_tree_fast_path_permanently_disabled",
                        extra={"reason": type(exc).__name__, "container_id": container_id},
                    )
                else:
                    logger.debug("dump_tree_fast_path_failed_falling_back", exc_info=True)
                result = _dump_tree_recursive(self._com, 0, effective_depth_cap)
                path = "slow"
```

Replace with:

```python
        # Decide whether to ATTEMPT the fast path on this call.
        # Precedence: per-call ``use_fast_path`` kwarg > module flag.
        # See sapsucker#23 — environments hitting the SAP Note 3674808
        # native crash bug can disable the fast path globally via the
        # SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH env var, or
        # per-call via ``use_fast_path=False``.
        if use_fast_path is False:
            fast_path_attempt = False
        elif use_fast_path is True:
            fast_path_attempt = True
        else:
            fast_path_attempt = not _fast_path_permanently_disabled

        # Fast path: bulk-read via GuiSession.GetObjectTree.
        # Skip the attempt entirely if (a) the caller or env disabled it,
        # (b) a previous call has already proven the fast path is permanently
        # unsupported on this process's SAP version (e.g. SAP GUI < 7.70 PL3),
        # or (c) we couldn't read the container ID. See
        # ``_fast_path_permanently_disabled``.
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
                    logger.warning(
                        "dump_tree_fast_path_permanently_disabled",
                        extra={"reason": type(exc).__name__, "container_id": container_id},
                    )
                else:
                    logger.debug("dump_tree_fast_path_failed_falling_back", exc_info=True)
                result = _dump_tree_recursive(self._com, 0, effective_depth_cap)
                path = "slow"
```

The `global _fast_path_permanently_disabled` declaration at the top of the function (currently at line 527) STAYS where it is. Do not move it.

- [ ] **Step 2.7: Update the `dump_tree` docstring to mention the new kwarg**

Find the existing docstring of `dump_tree` (starting at line 495) and add a new section after the existing `Args:` block. The current `Args` section reads:

```
        Args:
            max_depth: Maximum recursion depth. None means unlimited (with a
                       hard safety cap of 200 to prevent infinite recursion).
```

Replace with:

```
        Args:
            max_depth: Maximum recursion depth. None means unlimited (with a
                       hard safety cap of 200 to prevent infinite recursion).
            use_fast_path: Per-call override for the GetObjectTree fast path
                       (keyword-only). ``None`` (default) respects the
                       process-level decision: enabled by default, disabled
                       if ``SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH`` is
                       set in the environment, or disabled if a previous
                       call observed an AttributeError. ``True`` forces the
                       fast path attempt for this call only. ``False`` forces
                       the slow path for this call only. The kwarg does NOT
                       mutate the process-level decision; subsequent calls
                       with the default ``None`` see the unchanged module
                       flag. See sapsucker#23 for context.
```

- [ ] **Step 2.8: Run the new test class to verify all 13 tests now pass**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/test_get_object_tree.py::TestDumpTreeFastPathOptOut -v
```

Expected: all 13 tests pass.

- [ ] **Step 2.9: Run the FULL `test_get_object_tree.py` to verify no regression in existing tests**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/test_get_object_tree.py -v
```

Expected: every existing test still passes (37 + autouse-fixture-affected ones), the 21 new helper tests pass, and the 13 new opt-out tests pass. Total ~71 tests.

If any existing test fails, STOP. The autouse fixture should have isolated env-var state for every test. If you see `_fast_path_permanently_disabled` being unexpectedly True at the start of a test, the autouse fixture is broken.

- [ ] **Step 2.10: Run `test_base.py` and any other test file that imports from `base` to catch cross-file regressions**

```bash
cd C:/github/sapsucker && tox -e tests -- unittests/ -v
```

Expected: all tests pass.

- [ ] **Step 2.11: Commit**

```bash
cd C:/github/sapsucker && git add src/sapsucker/components/base.py unittests/test_get_object_tree.py && git commit -m "$(cat <<'EOF'
fix(base): wire SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH env var and per-call kwarg

Closes (in spirit; PR uses Refs not Closes) sapsucker#23.

The 0.4.0 GuiSession.GetObjectTree fast path triggers a documented
native crash on sustained-load workloads (SAP Note 3674808). A native
segfault inside sapgui.exe cannot be caught by Python try/except, so
the existing per-call fallback is ineffective.

This commit adds two escape hatches that let environments hitting
the crash force the slow path:

1. Env var SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH=1 (case-insensitive
   allowlist: 1/true/yes/on) sets the initial value of the existing
   _fast_path_permanently_disabled module flag at import time. The
   flag's existing AttributeError-cache semantics are preserved.

2. New keyword-only kwarg use_fast_path on GuiVContainer.dump_tree:
   - None (default): respect module flag
   - True: force fast path attempt for this call only
   - False: force slow path for this call only
   The kwarg does NOT mutate the module flag.

The fast path stays ENABLED BY DEFAULT — no perf regression for any
existing 0.4.0 caller. The downstream sapwebgui.mcp test environment
that hit the crash will opt out via tox.ini in a follow-up PR.

Adds 13 new unit tests covering the env var allowlist, the kwarg
precedence, the runtime AttributeError interaction, and the
keyword-only enforcement. All COM-free, no real SAP needed.
The existing 37 tests in test_get_object_tree.py and test_base.py
keep passing unchanged.

Refs Hochfrequenz/sapsucker#23.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Quality gates

**Files:**
- All previously modified files

### Steps

- [ ] **Step 3.1: Run mypy strict**

```bash
cd C:/github/sapsucker && tox -e type_check
```

Expected: `Success: no issues found in N source files`. If any new mypy errors fire on the changes from Task 2, fix them in `src/sapsucker/components/base.py` and re-run.

Common issues that might come up:
- The `use_fast_path: bool | None = None` annotation should be fine on Python 3.11+ — verify the file's `from __future__ import annotations` line is present.
- The `_read_fast_path_disabled_from_env() -> bool` annotation is unambiguous.

- [ ] **Step 3.2: Run pylint**

```bash
cd C:/github/sapsucker && tox -e linting
```

Expected: 10.00/10. Any new warnings should be evaluated:
- A `too-many-branches` warning on `dump_tree` is plausible — if it fires, add `# pylint: disable=too-many-branches` on the `def dump_tree(` line and add a comment explaining why (the precedence logic genuinely needs the branches).
- An `invalid-name` warning on `_SAPSUCKER_DISABLE_FAST_PATH_ENV` should NOT fire (it's a constant in UPPER_SNAKE_CASE), but if it does, the existing `# pylint: disable=invalid-name` pattern on the existing module-level vars is the precedent.
- A `global-statement` warning on the `dump_tree` `global` declaration should NOT fire because the existing code already uses `# noqa: PLW0603  pylint: disable=global-statement` — keep that comment intact.

- [ ] **Step 3.3: Run black + isort check**

```bash
cd C:/github/sapsucker && tox -e formatting
```

Expected: clean. If black wants to reformat, run `black src/sapsucker unittests` and re-check. If isort wants to reorder imports, run `isort src/sapsucker unittests` and re-check.

- [ ] **Step 3.4: Run spell check**

```bash
cd C:/github/sapsucker && tox -e spell_check
```

Expected: clean. New words like `SAPSUCKER`, `GETOBJECTTREE`, `dropdown`, `monkeypatch` are likely already in the dict (sapsucker has codespell + an ignore list). If a new word fires, add it to `domain-specific-terms.txt` (the existing ignore list referenced by tox.ini).

- [ ] **Step 3.5: Run the full unit suite one more time as a final sanity check**

```bash
cd C:/github/sapsucker && tox -e tests
```

Expected: every test passes. Final count should be ~71+ tests (37 existing + 21 helper parametrized + 13 opt-out).

- [ ] **Step 3.6: If any of steps 3.1–3.4 produced changes, commit them**

```bash
cd C:/github/sapsucker && git status
# If there are modifications:
git add <files>
git commit -m "$(cat <<'EOF'
style(base): satisfy linters and formatters after sapsucker#23 fix

No behavior change. Addresses lint/format/type-check feedback from
the quality-gate sweep.

Refs Hochfrequenz/sapsucker#23.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

If there are NO changes from the quality gates, skip this step.

---

## Task 4: Manual real-SAP regression check

**Files:**
- Temporary edit to `C:/github/sapwebgui.mcp/tox.ini` (will be reverted, NOT committed in this PR)

### Steps

This task validates the end-to-end fix on a real SAP system. It is not a CI gate; it is a one-shot manual confirmation that the integration suite goes green when the env var is set. The result feeds the PR body for sapsucker#23.

- [ ] **Step 4.1: Install the local sapsucker branch into the sapwebgui.mcp tox env**

```bash
cd C:/github/sapwebgui.mcp && .tox/integration_tests/Scripts/python.exe -m pip install -e C:/github/sapsucker --force-reinstall --no-deps
```

The `--no-deps` flag avoids re-resolving the dep tree (which could downgrade pydantic). Verify the installed version is the dev install pointing at the local checkout:

```bash
cd C:/github/sapwebgui.mcp && .tox/integration_tests/Scripts/python.exe -c "import sapsucker; print(sapsucker.__file__)"
```

Expected output: a path under `C:/github/sapsucker/src/sapsucker/__init__.py` (the editable install), NOT `.tox/integration_tests/Lib/site-packages/sapsucker/`.

- [ ] **Step 4.2: Verify the new env-var symbol is importable from the installed sapsucker**

```bash
cd C:/github/sapwebgui.mcp && .tox/integration_tests/Scripts/python.exe -c "from sapsucker.components.base import _read_fast_path_disabled_from_env; print('OK', _read_fast_path_disabled_from_env())"
```

Expected: `OK False` (env var unset → False).

- [ ] **Step 4.3: Add the env var to `tox.ini`'s `[testenv:integration_tests]` block**

Edit `C:/github/sapwebgui.mcp/tox.ini` and add to the `[testenv:integration_tests]` section, in the existing `setenv =` block (or add the block if it doesn't exist):

```
setenv =
    PYTHONPATH = {toxinidir}/src
    SAPSUCKER_DISABLE_GETOBJECTTREE_FAST_PATH = 1
```

This is a TEMPORARY edit. Do NOT commit it as part of this PR — it belongs in the follow-up sapwebgui.mcp PR after 0.4.1 is published.

- [ ] **Step 4.4: Kill any stale SAP processes from prior runs**

```bash
taskkill //F //IM saplogon.exe 2>&1 || true
```

- [ ] **Step 4.5: Run the full integration suite**

```bash
cd C:/github/sapwebgui.mcp && tox -e integration_tests 2>&1 | tail -50
```

Expected: the suite completes WITHOUT a native crash (no `STATUS_ACCESS_VIOLATION`, no exit code `3221225477`). Some tests may still fail for unrelated SAP-state reasons (the suite is flaky against a real backend) — what we need is the absence of the **specific** crash mode at `test_bp_get_dropdown_options_anrede`.

If the suite still crashes natively at the same test with the env var set, STOP. The fix did not work end-to-end and the assumption that GetObjectTree is the trigger is wrong. Report findings to the maintainer; the spec's "Failure mode 1" predicted exactly this case.

- [ ] **Step 4.6: Revert the temporary `tox.ini` edit**

```bash
cd C:/github/sapwebgui.mcp && git checkout tox.ini
```

Verify nothing is staged from the temporary edit:

```bash
cd C:/github/sapwebgui.mcp && git status
```

The `tox.ini` file should be back to its committed state.

- [ ] **Step 4.7: Note the result for the PR body**

Capture the integration suite outcome (exit code, total elapsed time, pass/fail counts, presence-or-absence of native crash) for use in the sapsucker#23 PR description. The PR body should explicitly state whether step 4.5 succeeded — that's the proof point for the maintainer that the fix works end-to-end.

---

## Verification checklist (run after Task 4)

Before declaring the implementation done:

- [ ] **All Definition of Done items from the spec are satisfied** (re-read the spec's "Definition of Done" section, section 3.5–3.6 covers items 5/10, Task 4 covers item 8, all other items should be naturally satisfied by the code changes)
- [ ] `git log --oneline fix/issue-23-fast-path-opt-out` shows the expected commit sequence (1 spec, 1 spec revision, 1 infrastructure, 1 fix, 0–1 lint cleanup)
- [ ] `git status` is clean — no uncommitted files, no temporary edits leaked
- [ ] The branch is ready for the next task (#70: open the PR)

---

## Notes for the implementer

- **Do not skip the baseline run in Task 1.** It's a freebie that catches a stale starting state and saves an hour of debugging.
- **TDD discipline matters here.** Step 2.2 explicitly says "run the tests and confirm they fail". If you skip it and the tests pass without the implementation, you have a false-positive test that doesn't actually exercise the code you wrote.
- **The autouse fixture is load-bearing.** If you find yourself debugging a flaky `_fast_path_permanently_disabled` test, the first thing to check is whether the fixture is doing what it claims. Print `_base_module._fast_path_permanently_disabled` at the start of the failing test.
- **Don't refactor the existing comment block.** The spec is explicit: edit in place, don't rewrite. Keep the existing prose verbatim and append the new paragraph.
- **The `global` statement at the top of `dump_tree` stays where it is.** If you find yourself reaching for the `global` keyword inside the `else` branch, stop — you don't need it because it's already declared at function-top.
- **Manual SAP test in Task 4 is genuinely manual.** It can't be automated in CI because CI doesn't have a SAP system. Don't try to make it a pytest test.
