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
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from sapsucker import SapGui
from sapsucker._errors import SapConnectionError, ScriptingDisabledError
from sapsucker.monitor import SessionMonitor, Watch

app = typer.Typer(
    add_completion=False,
    help="Sample a live SAP GUI session alongside a recording.",
    no_args_is_help=False,
)


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
    try:
        watches = [Watch.parse(raw) for raw in (watch or [])]
    except ValueError as exc:
        typer.secho(f"bad --watch: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    session = _attach()
    info = session.info
    typer.echo(f"attached: {info.system_name} client {info.client} as {info.user}")
    for w in watches:
        typer.echo(f"watching: {w.element_id} .{w.prop}")
    typer.echo(f"sampling every {interval}s -> {out}   (Ctrl+C to stop)")

    monitor = SessionMonitor(session, watches=watches, interval=interval)
    written = 0
    changes = 0

    with out.open("w", encoding="utf-8") as handle:
        try:
            for sample in monitor.samples():
                handle.write(json.dumps(sample.as_record(), ensure_ascii=False) + "\n")
                handle.flush()  # survive a hard Ctrl+C
                written += 1
                if sample.changed or sample.seq == 0:
                    if sample.seq:
                        changes += 1
                    gap = "" if sample.gap_since_change_s is None else f"  (+{sample.gap_since_change_s:.1f}s)"
                    focus = str(sample.values.get("focus_id", "?"))
                    typer.echo(
                        f"  [{sample.elapsed_s:>7.3f}] "
                        f"{sample.values.get('transaction', '?')}/{sample.values.get('screen_number', '?')}  "
                        f"focus={focus[-42:]}  "
                        f"changed={','.join(sample.changed) or 'baseline'}{gap}"
                    )
        except KeyboardInterrupt:
            typer.echo()

    typer.echo(f"wrote {written} sample(s), {changes} change(s) to {out}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
