"""Typer CLI for :mod:`sapsucker.monitor`.

Kept separate from the library so ``typer`` stays an optional dependency:
``pip install sapsucker[cli]``. Importing :mod:`sapsucker.monitor` never pulls
typer in.

Run this beside SAP GUI's built-in recorder (``Alt+F12`` -> *Script Recording
and Playback*): start the monitor, start the recorder, do the task, stop the
recorder, stop the monitor. Hand over the ``.vbs`` and the ``.jsonl`` together.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from sapsucker import SapGui
from sapsucker._errors import SapConnectionError, ScriptingDisabledError
from sapsucker.monitor import ABSENT, UNREADABLE, SessionMonitor, Watch

app = typer.Typer(
    add_completion=False,
    help="Sample a live SAP GUI session alongside a recording.",
    no_args_is_help=False,
)


def _selftest() -> None:
    """Prove the COM stack is present, then exit.

    The no-SAP path cannot distinguish a missing SAP GUI from a binary built
    without pywin32 — ``_com.py`` swallows the pywin32 ``ImportError`` and both
    surface as ``SapConnectionError``. So a frozen binary with no COM support at
    all would pass an end-to-end smoke test. This checks directly.
    """
    try:
        import pythoncom  # type: ignore[import-untyped]  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        import win32com.client  # type: ignore[import-untyped]  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        typer.secho(f"selftest: COM stack unavailable: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc
    typer.echo(f"selftest: com ok ({pythoncom.__file__}, {win32com.client.__name__})")
    raise typer.Exit(code=0)


def _attach() -> Any:
    """Attach to the first session of the first connection, or exit with a diagnostic."""
    try:
        app_ = SapGui.connect()
    except ScriptingDisabledError as exc:
        typer.secho(f"SAP GUI Scripting is not available: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except SapConnectionError as exc:
        typer.secho(
            f"Cannot reach SAP GUI: {exc}\n  Start SAP GUI for Windows and log in, then retry.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    connections = app_.connections
    if len(connections) == 0:
        typer.secho(
            "SAP GUI is running but has no open connection.\n"
            "  - Open a connection in SAP Logon and log in, then retry.\n"
            "  - If you are logged in, check RZ11 sapgui/user_scripting. A value set only\n"
            "    by dynamic switch reverts when the instance restarts, and presents as\n"
            "    zero connections rather than as a scripting error.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if len(connections) > 1:
        typer.secho(f"note: {len(connections)} connections open; using the first.", fg=typer.colors.YELLOW, err=True)

    sessions = connections[0].sessions  # type: ignore[attr-defined]
    if len(sessions) == 0:
        typer.secho(
            "The connection has no session (still logging in, or a ghost connection).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    return sessions[0]


@app.command()
def main(
    out: Annotated[Path, typer.Option("--out", "-o", help="JSONL output path.")] = Path("timing.jsonl"),
    interval: Annotated[float, typer.Option("--interval", "-i", min=0.01, help="Seconds between samples.")] = 0.2,
    selftest: Annotated[
        bool,
        typer.Option("--selftest", hidden=True, help="Verify this binary can import its COM stack, then exit."),
    ] = False,
    watch: Annotated[
        list[str] | None,
        typer.Option(
            "--watch",
            "-w",
            metavar="ID:PROP",
            help="Also sample a COM property, e.g. 'wnd[0]/shellcont/shell:FirstVisibleRow'. Repeatable.",
        ),
    ] = None,
) -> None:
    """Sample the live session until interrupted, writing one JSON object per sample."""
    if selftest:
        _selftest()
    try:
        watches = [Watch.parse(raw) for raw in (watch or [])]
    except ValueError as exc:
        typer.secho(f"bad --watch: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    session = _attach()
    try:
        info = session.info
        typer.echo(f"attached: {info.system_name} client {info.client} as {info.user}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        typer.secho(f"attached, but session metadata is unreadable: {exc}", fg=typer.colors.YELLOW, err=True)

    monitor = SessionMonitor(session, watches=watches, interval=interval)

    # 9: probe each watch once so a misspelled property is reported now rather
    # than reading <unreadable> for the whole journey.
    if watches:
        probe = monitor.read_once()
        for w in watches:
            value = probe.get(w.key)
            if value == UNREADABLE:
                hint = "  (unreadable — check the property name)"
            elif value == ABSENT:
                hint = "  (not on the current screen — fine if you navigate to it later)"
            else:
                hint = ""
            typer.echo(f"watching: {w.element_id} .{w.prop} = {value}{hint}")

    if out.exists():
        typer.secho(f"note: overwriting {out}", fg=typer.colors.YELLOW, err=True)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        handle = out.open("w", encoding="utf-8")
    except OSError as exc:
        typer.secho(f"cannot write {out}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"sampling every {interval}s -> {out}   (Ctrl+C to stop)")
    written = 0
    changes = 0
    stopped: str | None = None

    with handle:
        try:
            for sample in monitor.samples():
                handle.write(json.dumps(sample.as_record(), ensure_ascii=False) + "\n")
                handle.flush()  # survive a hard Ctrl+C
                written += 1
                if sample.changed or sample.seq == 0:
                    if sample.seq:
                        changes += 1
                    gap = (
                        ""
                        if sample.gap_since_change is None
                        else f"  (+{sample.gap_since_change.total_seconds():.1f}s)"
                    )
                    focus = str(sample.values.get("focus_id", "?"))
                    typer.echo(
                        f"  [{sample.elapsed.total_seconds():>7.3f}] "
                        f"{sample.values.get('transaction', '?')}/{sample.values.get('screen_number', '?')}  "
                        f"focus={focus[-42:]}  "
                        f"changed={','.join(sample.changed) or 'baseline'}{gap}"
                    )
        except KeyboardInterrupt:
            typer.echo()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Report the count first: the log written so far is still usable, and
            # it is the user's only confirmation that it pairs with a recording.
            stopped = f"{exc.__class__.__name__}: {exc}"

    typer.echo(f"wrote {written} sample(s), {changes} change(s) to {out}")
    if stopped is not None:
        typer.secho(f"monitoring stopped early: {stopped}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()  # typer calls sys.exit itself (click standalone_mode)
