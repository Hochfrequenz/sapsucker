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

When the question is "does this member exist", the SAP GUI Scripting API guide and the installed type library can disagree, and the library wins for the version in use. `scripts/dump_type_library.py` and `scripts/diff_typelib.py` exist for this; see [`docs/coverage-gaps.md`](docs/coverage-gaps.md). All three land in #97, so this file must not merge before it or these become dead links.

This is not hypothetical. A documentation-based pass once produced a finding that `GuiToolbar` was missing twelve methods, seven of them `GetButton*`. The type library shows `ISapToolbarTarget` exposes no own members at all — the guide documents all twelve on `GuiToolbarControl`, where sapsucker already wraps eleven of them, every `GetButton*` included (`src/sapsucker/components/shell.py:104`–`:144`). Filing that would have been a wrong issue about members the package already had.

### Record what could not be verified

Some things resist testing. Say so in the code, not just in a PR comment.

`sapsucker.monitor`'s known-limits list records that its graceful-degradation path is unit-tested but never reproduced live: three attempts failed because closing a SAP mode leaves the COM proxy valid, and closing SAP GUI raises its own confirmation dialog that keeps the session alive. That is more useful to the next reader than a claim of coverage.

## Tests

**Every assertion must be shown able to fail.** Break the code, watch the test fail, restore. A test that passes against a broken implementation is decoration, and this has bitten repeatedly:

- A pause-gap assertion checked only `gap is not None and gap >= timedelta(0)`, so a mutation measuring the gap from monitor start instead of from the previous change kept all twenty tests green.
- Nothing asserted the *value* of `busy`, so mutating it to a COM name `GuiSession` does not have still passed every test. It would have put `<unreadable>` in every log row forever.
- An assertion joined with `or` (`"Traceback" not in x or "SystemExit" not in x`) could not fail at all. This one shipped, in `0a9d470`, and was fixed in `56b4daa`.

The first two were caught by deliberately mutating the code, not by the suite. That is the point: the suite said green in all three cases.

**Fakes must match reality.** A fake that models the wrong read order or the wrong return shape produces confident, wrong results. Check a hand-written fake against recorded fixtures or the real API before trusting a test built on it.

**Integration tests skip silently** — on CI, when `SAP_SKIP_INTEGRATION` is set, when `.env` credentials are missing, or on any non-Windows machine. There is no allowlist: `is_sap_integration_test_machine()` in `unittests/conftest.py` returns true by default and subtracts from there. The predicate lives there; the skips themselves are module-level `pytestmark`s in each `test_*_integration.py`. A skipped test looks like a pass in the summary line. Use `-rs` to see skip reasons, and never treat "the suite is green" as evidence an integration test ran.

## Gates

Run each one and read its exit code individually. Chaining them hides failures behind the last command's status — three `codespell` calls chained once hid a failure in `src`.

`pyproject.toml` sets `default-groups = []`, so a fresh checkout has no dev tooling installed. The three `ruff` lines happen to work anyway; `codespell`, `mypy` and `pytest` die with `Failed to spawn: <tool>` — which is a *setup* failure that looks nothing like a gate failure, and is easy to skim past when six of ten lines error identically. `README.md` gets this right and this file used not to. Sync first:

```bash
uv sync --group dev   # dev includes tests, linting, type_check, coverage, spell_check

uv run ruff check src/sapsucker examples
uv run ruff format --check .
uv run ruff check --select I .
uv run codespell --ignore-words=domain-specific-terms.txt src
uv run codespell --ignore-words=domain-specific-terms.txt examples
uv run codespell --ignore-words=domain-specific-terms.txt README.md
uv run codespell --ignore-words=domain-specific-terms.txt AGENTS.md
uv run mypy --show-error-codes src/sapsucker --strict
uv run mypy --show-error-codes examples/sapsucker --strict --ignore-missing-imports
uv run pytest
```

That is ten commands — four `codespell` invocations and two `mypy` invocations, not one each. Then an eleventh, `coverage`, which is a required check and the one that can fail on a PR where all ten above pass: add uncovered code and `pytest` stays green.

```bash
uv run coverage run -m pytest
uv run coverage report --fail-under 90 --omit "unittests/*,scripts/*"
```

`scripts/` is omitted because it is tooling rather than package code and is ungated everywhere else; its SAP-side branches are unreachable on a runner, so measuring it would report on CI's environment rather than on the code.

Note `mypy --strict` needs the optional CLI dependency present, so `type_check` includes it — an absent dependency reports as `import-not-found` plus `untyped-decorator` rather than as a missing extra.

## Check names are load-bearing

The branch ruleset requires status checks by exact name, so a check that renames itself can never be satisfied again.

**An explicit `name:` is not enough — it has to reference the matrix.** A matrix job's reported name gets every matrix value appended unless its `name:` interpolates them itself. `codeql-analysis.yml` is the counterexample to have in mind: it carries `name: Analyze` and still reports as `Analyze (python)`. Adding a fixed `name:` to a matrix job and believing the name is now pinned is how three required checks get broken at once.

Appending is only *dangerous* where a matrix value is a command string, because then editing the command renames the check. `unittests.yml` has no `name:` at all and its contexts (`pytest (3.11, windows-latest)` and friends) are perfectly stable, since its matrix values are Python versions. `pythonlint.yml` and `formatting.yml` interpolate `${{ matrix.… }}` into their names for exactly this reason; do not remove those pins.

`format (black)` and `format (isort)` are ruff under deliberately stale names, kept only so the ruleset's contexts keep resolving. Renaming them blocks every PR.

## Documentation is code

Examples in `README.md` and in docstrings are the package's most-read surface, and they are not type-checked. Two shipped examples **still** teach an unpaged ALV read that silently truncates large grids (`README.md`, `examples/sapsucker/alv_grid_export.py`) — see #91, unfixed while the paging design is open — and a rename once left two example blocks raising `AttributeError`. If you change an API, grep the docs for the old name.

## Review

Nothing here is optional, and none of it was invented in the abstract — each rule is a thing that shipped wrong once.

**Every PR: draft → round 1 → fix → round 2 → Copilot → fix → all gates green → CI green → mark ready.** Exactly two internal rounds. Round 2 is terminal, and the reviewer's job there is to *suggest fixes*, not only to report blockers — a round that ends in a list of concerns nobody acted on has cost time and changed nothing. Re-review after any substantial later edit; the rule is per change, not per PR.

**Keep PRs small.** Two rounds over a large diff finds less than two rounds over a small one, and the second round is the one that catches the subtle thing.

**Fact-check prose before posting it.** Issue and PR bodies are the record other people act on, so they get the same scrutiny as code, from a reviewer that did not write them. This is not theoretical: four published claims in one session were wrong, including a paging loop that never scrolled and — after the fix — a second loop whose termination condition permitted an infinite loop.

**Correct published text in place** (`gh issue edit N --body-file`, `gh pr edit N --body-file`). Never append a correcting comment: a reader who stops at the body gets the wrong version, and the thread ends up reading as two unreconciled arguments.

**Read an issue's whole comment history before commenting on it.** Adding a third position to a question that already has two is how #41 got into its current state.

**Copilot findings get a reply and a resolve, false positives included.** Silent dismissal loses the reasoning, and the next person re-litigates it. Poll Copilot by `commit_id`, not by review count — a force-push leaves a stale review attached to the old SHA, so a review can exist while none of it applies to head.

**Verify provenance before asserting it** (`git log -S<symbol>`). "This was added because a journey needed it" has been claimed twice and been false twice; both were in the initial bulk wrap.

**Name examples, never bare aggregates.** "Three components are affected" is unfalsifiable and has been wrong; "`GuiComboBox`, `GuiGridView` and `GuiStatusbar`" can be checked.

**Squash-merge once Copilot is clean and CI is green.** For stacked PRs, merge in dependency order and rebase the base branch of each one after the one below it lands.
