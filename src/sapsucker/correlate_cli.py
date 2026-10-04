"""Typer CLI for :mod:`sapsucker._correlate`.

Kept separate from the library so ``typer`` stays an optional dependency,
mirroring :mod:`sapsucker.monitor_cli`. The library takes a normalized
transcript stream; the STT format choice lives here. SRT only for now —
whisper JSON joins when someone needs it (YAGNI).

Example::

    sapsucker-correlate journey3.vbs timing.jsonl -m timeline.md
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer

from sapsucker._correlate import (
    TranscriptEntry,
    correlate,
    load_monitor_log,
)
from sapsucker._recording import Recording

app = typer.Typer(
    add_completion=False,
    help="Join a recorded .vbs, a monitor JSONL and an optional transcript into one timeline.",
    no_args_is_help=False,
)

_SRT_TIME = re.compile(
    r"^(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})\s*-->\s*"
    r"(?P<h2>\d+):(?P<m2>\d{2}):(?P<s2>\d{2})[,.](?P<ms2>\d{3})"
)


def _srt_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(text: str) -> tuple[TranscriptEntry, ...]:
    """Parse an SRT file into normalized transcript entries.

    Raises:
        ValueError: On a cue line that does not match the SRT timestamp shape.
    """
    entries: list[TranscriptEntry] = []
    current: tuple[float, float] | None = None
    chunks: list[str] = []
    for raw in [*text.splitlines(), ""]:
        line = raw.strip("﻿").strip()
        if current is not None and not line:
            entries.append(TranscriptEntry(current[0], current[1], " ".join(chunks)))
            current, chunks = None, []
            continue
        if current is None:
            m = _SRT_TIME.match(line)
            if m:
                current = (
                    _srt_seconds(m["h"], m["m"], m["s"], m["ms"]),
                    _srt_seconds(m["h2"], m["m2"], m["s2"], m["ms2"]),
                )
            elif line.isdigit() or not line:
                continue
            else:
                raise ValueError(f"not an SRT timestamp line: {line!r}")
        else:
            chunks.append(line)
    return tuple(entries)


@app.command()
def main(
    recording: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Recorded .vbs from SAP GUI's recorder.")
    ],
    monitor_log: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="JSONL from sapsucker-monitor.")],
    transcript: Annotated[
        Path | None, typer.Option("--transcript", "-t", help="Optional narration transcript (SRT).")
    ] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="JSONL timeline output path.")] = Path("timeline.jsonl"),
    markdown: Annotated[
        Path | None, typer.Option("--markdown", "-m", help="Also write a human-readable markdown timeline.")
    ] = None,
) -> None:
    """Correlate a recording with its monitor log into one timestamped timeline."""
    rec = Recording.load(recording)
    log = load_monitor_log(monitor_log.read_text(encoding="utf-8").splitlines())
    entries: tuple[TranscriptEntry, ...] = ()
    if transcript is not None:
        try:
            entries = parse_srt(transcript.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            typer.secho(f"bad --transcript: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc

    timeline = correlate(rec, log, transcript=entries)

    out.write_text(timeline.to_jsonl(), encoding="utf-8")
    typer.echo(
        f"{len(timeline.steps)} step(s) -> {out}   "
        + " ".join(f"{n} {k}" for k, n in sorted(timeline.strategy_counts.items()))
    )
    if markdown is not None:
        markdown.write_text(timeline.to_markdown(), encoding="utf-8")
        typer.echo(f"markdown -> {markdown}")
    unmatched = timeline.strategy_counts.get("unmatched", 0)
    if unmatched:
        typer.secho(
            f"note: {unmatched} step(s) could not be timestamped (strategy 'unmatched') — "
            "the monitor log may not cover the whole recording.",
            fg=typer.colors.YELLOW,
            err=True,
        )


if __name__ == "__main__":  # pragma: no cover
    app()  # typer calls sys.exit itself (click standalone_mode)
