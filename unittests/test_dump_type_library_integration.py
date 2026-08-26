"""Integration test for ``scripts/dump_type_library.py`` against a real SAP GUI.

CI cannot reach any of this: the Linux and Windows runners have no SAP GUI
installed. It needs no session and no login, though — the type library is
static — so it is the cheapest SAP-side check in the suite, and the one to run
after touching either coverage script.

Run it::

    uv run pytest unittests/test_dump_type_library_integration.py -rs -v

``-rs`` is not optional. Off an authorized machine every SAP-side test here
skips, and a skip reads as a pass in the summary line. If a test reports
``skipped`` rather than ``passed``, **nothing about SAP was verified by it**.

What it establishes that the unit suite cannot:

* ``--typelib <path>`` really loads a type library. That option was added
  because the failure message had been telling users to pass an OCX path for a
  release in which ``argparse`` had no such option; the code path itself had
  never run.
* It loads *the same* library the default probe finds, so the escape hatch is
  not a differently-shaped one that silently dumps something else.
* The JSON the dumper writes is the JSON ``diff_typelib.py`` reads — on real
  data, end to end, rather than on a hand-written fixture that could model the
  wrong shape.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from unittests.conftest import is_sap_integration_test_machine

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="SAP GUI COM is Windows-only")
authorized_only = pytest.mark.skipif(
    not is_sap_integration_test_machine(),
    reason="SAP integration tests only run on authorized machines",
)


def _load_script(name: str) -> ModuleType:
    """Import a file from scripts/, which is not a package and not on sys.path."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ocx() -> Path:
    """The installed sapfewse.ocx, read from the script's own candidate list.

    Reading the script's list rather than repeating one keeps the test from
    passing against a path the script would never have tried.
    """
    dump = _load_script("dump_type_library")
    for candidate in dump.OCX_CANDIDATES:
        if candidate.exists():
            return candidate
    pytest.skip(f"no sapfewse.ocx at any of: {', '.join(str(c) for c in dump.OCX_CANDIDATES)}")


# ---------------------------------------------------------------------------
# Runs everywhere, CI included: the guard needs neither SAP GUI nor pywin32.
# ---------------------------------------------------------------------------


def test_a_missing_explicit_path_fails_loudly_and_names_it(tmp_path: Path) -> None:
    """An explicit --typelib is an instruction, not a hint.

    Falling through to the install-path guess would dump a *different*
    installation's library under the name the user asked for — a wrong answer
    shaped exactly like a right one. The path has to appear in the message, or
    the user cannot tell a typo from a missing install.
    """
    dump = _load_script("dump_type_library")
    missing = tmp_path / "nowhere" / "sapfewse.ocx"

    with pytest.raises(SystemExit) as excinfo:
        dump._load_type_lib(missing)

    assert str(missing) in str(excinfo.value)


# ---------------------------------------------------------------------------
# Real SAP GUI installation required below here.
# ---------------------------------------------------------------------------


@windows_only
@authorized_only
def test_explicit_typelib_path_loads_a_real_library(ocx: Path) -> None:
    """The added code path, exercised against the installed OCX."""
    dump = _load_script("dump_type_library")

    lib = dump._load_type_lib(ocx)

    count = lib.GetTypeInfoCount()
    # `is not None` would pass on a library that loaded but exposes nothing.
    assert count > 50, f"only {count} type infos — that is not the Scripting API library"
    names = {lib.GetDocumentation(i)[0] for i in range(count)}
    missing = {"GuiSession", "GuiComboBox", "GuiGridView"} - names
    assert not missing, f"loaded a library without {sorted(missing)}"


@windows_only
@authorized_only
def test_explicit_path_and_default_probe_find_the_same_library(ocx: Path) -> None:
    """--typelib must be an escape hatch, not a second, different source.

    If the two disagree, a report produced with --typelib describes an
    installation other than the one the default probe would have read, and
    nothing in the output says so.
    """
    dump = _load_script("dump_type_library")

    explicit = dump._load_type_lib(ocx)
    default = dump._load_type_lib()

    assert explicit.GetDocumentation(-1)[0] == default.GetDocumentation(-1)[0]
    assert explicit.GetTypeInfoCount() == default.GetTypeInfoCount()


@windows_only
@authorized_only
def test_the_dump_feeds_the_diff(ocx: Path, tmp_path: Path) -> None:
    """End to end on real data: dump_type_library.py -> diff_typelib.py.

    The two scripts share an undeclared JSON contract. `diff_typelib` exits 2
    when the base interfaces it subtracts are absent, and reports have=0 for
    everything when the class regions do not match — a contract drift surfaces
    as either a hard 2 or a confident wrong answer, and only a real dump tells
    them apart.

    It runs the diff from ``tmp_path``, not the repo root, which is also what
    proves ``diff_typelib``'s ``SRC`` is anchored to the script rather than to
    the working directory.
    """
    out = tmp_path / "typelib.json"

    dumped = subprocess.run(
        [sys.executable, str(SCRIPTS / "dump_type_library.py"), "--typelib", str(ocx), "-o", str(out)],
        capture_output=True,
        text=True,
        check=False,  # the assertion below reports stderr; check=True would hide it
    )
    assert dumped.returncode == 0, dumped.stderr

    data = json.loads(out.read_text(encoding="utf-8"))
    diff_mod = _load_script("diff_typelib")
    for base in diff_mod.BASE_INTERFACES:
        assert data["types"].get(base, {}).get("members"), (
            f"{base} carries no members, so diff_typelib.py would subtract nothing and exit 2"
        )

    diffed = subprocess.run(
        [sys.executable, str(SCRIPTS / "diff_typelib.py"), str(out)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,  # ditto: exit 2 is a documented outcome worth reporting
    )
    assert diffed.returncode == 0, diffed.stderr
    assert "own members only" in diffed.stdout

    # have=0 across the board is what a silently-broken diff looks like, and it
    # still exits 0. GuiComboBox is the anchor: sapsucker wraps it, so a working
    # run must credit it with something.
    combobox = [line for line in diffed.stdout.splitlines() if line.startswith("GuiComboBox ")]
    assert combobox, f"GuiComboBox absent from the report:\n{diffed.stdout}"
    have = int(combobox[0].split()[2])
    assert have > 0, f"GuiComboBox credited with 0 wrapped members — the diff matched nothing: {combobox[0]}"
