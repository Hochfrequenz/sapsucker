# Working on sapsucker

Conventions for agents (and humans) changing this package. The short version: **this library talks to software that only exists on a Windows desktop, so most of what matters cannot be verified by CI. Anything that *can* be checked against a real SAP GUI must be.**

## Verify against a real SAP GUI, do not infer

sapsucker wraps the SAP GUI Scripting COM API. CI runs on Linux and Windows runners with no SAP GUI installed, so it can prove that code imports, type-checks and passes tests against fakes — and almost nothing else.

**If a claim is about SAP's behaviour, it needs a run against a real system.** That includes:

- Does a COM member exist on the installed version, and what does it return?
- Does a wrapper actually work, as opposed to compiling?
- Does a screen behave as the documentation says?
- Does an error path produce the diagnostic it is supposed to?

Do not write "verified" on anything CI cannot reach. Write what was actually established and what was not.

### Ask for the run; give the whole command

A human has to do this, so make it a copy-paste block including the git steps. Never assume a working directory or branch state:

```powershell
git fetch origin
git checkout <branch>
git pull
uv sync --group tests --group cli_group
uv run python scripts/<script>.py -o out.json
```

State what output you need and what would count as a failure, so the run is not wasted. A script that only reports success leaves a crash indistinguishable from a quiet pass.

### Prefer the type library to the documentation

When the question is "does this member exist", the SAP GUI Scripting API guide and the installed type library can disagree, and the library wins for the version in use. `scripts/dump_type_library.py` and `scripts/diff_typelib.py` exist for this; see [`docs/coverage-gaps.md`](docs/coverage-gaps.md).

This is not hypothetical. A documentation-based pass once produced a finding that `GuiToolbar` was missing twelve `GetButton*` methods. The type library showed it exposes no own members at all — the guide documents those on `GuiToolbarControl`. Filing that would have been a wrong issue.

### Record what could not be verified

Some things resist testing. Say so in the code, not just in a PR comment.

`sapsucker.monitor`'s known-limits list records that its graceful-degradation path is unit-tested but never reproduced live: three attempts failed because closing a SAP mode leaves the COM proxy valid, and closing SAP GUI raises its own confirmation dialog that keeps the session alive. That is more useful to the next reader than a claim of coverage.

## Tests

**Every assertion must be shown able to fail.** Break the code, watch the test fail, restore. A test that passes against a broken implementation is decoration, and this has bitten repeatedly:

- A pause-gap assertion checked only `gap is not None and gap >= 0`, so rewriting the gap to measure from the wrong origin kept all tests green.
- `busy` was read through a COM name that does not exist, degrading silently to a sentinel in every log row, with all tests passing.
- An assertion joined with `or` (`"Traceback" not in x or "SystemExit" not in x`) could not fail at all.

**Fakes must match reality.** A fake that models the wrong read order or the wrong return shape produces confident, wrong results. Check a hand-written fake against recorded fixtures or the real API before trusting a test built on it.

**Integration tests skip silently** off an authorized machine (`unittests/conftest.py`). A skipped test looks like a pass in the summary line. Use `-rs` to see skip reasons, and never treat "the suite is green" as evidence an integration test ran.

## Gates

Run each one and read its exit code individually. Chaining them hides failures behind the last command's status:

```bash
uv run ruff check src/sapsucker examples
uv run ruff format --check .
uv run ruff check --select I .
uv run codespell --ignore-words=domain-specific-terms.txt src
uv run codespell --ignore-words=domain-specific-terms.txt examples
uv run codespell --ignore-words=domain-specific-terms.txt README.md
uv run mypy --show-error-codes src/sapsucker --strict
uv run mypy --show-error-codes examples/sapsucker --strict --ignore-missing-imports
uv run pytest
```

Note `mypy --strict` needs the optional CLI dependency present, so `type_check` includes it — an absent dependency reports as `import-not-found` plus `untyped-decorator` rather than as a missing extra.

## Check names are load-bearing

The branch ruleset requires status checks by exact name. A matrix job without an explicit `name:` gets every matrix value appended, so editing a command silently renames a required check and it can never be satisfied again. Both `pythonlint.yml` and `formatting.yml` pin their names for this reason; do not remove those pins.

## Documentation is code

Examples in `README.md` and in docstrings are the package's most-read surface, and they are not type-checked. Two shipped examples taught an unpaged ALV read that silently truncates large grids, and a rename left two example blocks raising `AttributeError`. If you change an API, grep the docs for the old name.
