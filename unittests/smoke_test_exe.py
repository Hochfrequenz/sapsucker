"""Smoke-test a built monitor .exe. Run: python unittests/smoke_test_exe.py dist/<binary>

A CI runner has no SAP GUI, so COM cannot be exercised. What IS testable is the
part that regressed during development: the no-SAP path. An early prototype
surfaced a missing connection as a bare IndexError, so this asserts the binary
fails cleanly with a diagnostic rather than a traceback.
"""

import subprocess
import sys
from pathlib import Path


def _run(binary: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=120, check=False)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    binary = Path(sys.argv[1])
    if not binary.exists():
        print(f"not found: {binary}", file=sys.stderr)
        return 2

    failures: list[str] = []

    help_result = _run(binary, "--help")
    if help_result.returncode != 0:
        failures.append(f"--help exited {help_result.returncode}\n{help_result.stderr}")
    for expected in ("--out", "--interval", "--watch"):
        if expected not in help_result.stdout:
            failures.append(f"--help output is missing {expected}")

    # A bad --watch must be rejected before any COM work is attempted.
    bad_watch = _run(binary, "--watch", "no-colon-here")
    if bad_watch.returncode != 2:
        failures.append(f"malformed --watch exited {bad_watch.returncode}, expected 2")
    if "element_id:ComProperty" not in (bad_watch.stderr + bad_watch.stdout):
        failures.append("malformed --watch did not explain the expected format")

    # No SAP GUI on a runner: must be a clean diagnostic, not a traceback.
    no_sap = _run(binary)
    combined = no_sap.stdout + no_sap.stderr
    if no_sap.returncode == 0:
        failures.append("running without SAP GUI exited 0; expected a failure")
    if "Traceback" in combined:
        failures.append(f"running without SAP GUI produced a traceback:\n{combined}")
    if "SAP GUI" not in combined:
        failures.append(f"running without SAP GUI gave no usable diagnostic:\n{combined}")

    if failures:
        print("SMOKE TEST FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"smoke test passed: {binary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
