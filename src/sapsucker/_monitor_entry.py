"""Console-script shim for ``sapsucker-monitor``.

The entry point is installed with the base package, but the CLI needs the
``cli`` extra. Without this shim, ``sapsucker-monitor`` on a plain
``pip install sapsucker`` would fail with a bare ``ModuleNotFoundError: typer``.
Deliberately imports nothing from :mod:`sapsucker.monitor_cli` at module level.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Run the monitor CLI, or explain how to install it."""
    try:
        from sapsucker.monitor_cli import app  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        print(
            f"sapsucker-monitor needs the CLI extra ({exc}).\n  Install it with:  pip install sapsucker[cli]",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    app()  # typer calls sys.exit itself (click standalone_mode)
