"""Trim a long ``sapsucker-monitor`` JSONL log to the samples the correlator can use.

The correlator reads only ``changed``, the values and the clock, so a log that is
mostly idle polling (``changed == []``) shrinks to a few dozen lines without
changing the timeline ``correlate`` produces. Kept: the baseline (the first
sample in the file, whatever its ``seq``), every sample with a non-empty
``changed``, and the sample immediately before each of those (so a reader can
see the before-state). Original ``seq`` and ``elapsed_s`` values are preserved.
Empty input gives empty output.

Usage::

    uv run python scripts/trim_monitor_log.py FULL.jsonl OUT.jsonl --through-seq 197
"""

import argparse
import json
import sys
from pathlib import Path


def trim(lines: list[str], through_seq: int | None) -> list[str]:
    """Return the kept lines, in order. Blank lines are ignored."""
    rows = [(line, json.loads(line)) for line in lines if line.strip()]
    keep: set[int] = {0} if rows else set()  # the first sample is the baseline
    for i, (_, d) in enumerate(rows):
        if through_seq is not None and d["seq"] > through_seq:
            break
        if d.get("changed"):
            keep.add(i)
            if i > 0:
                keep.add(i - 1)
    return [rows[i][0].rstrip("\n") for i in sorted(keep)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--through-seq", type=int, default=None, help="drop everything after this seq")
    args = ap.parse_args(argv)
    kept = trim(args.source.read_text(encoding="utf-8").splitlines(), args.through_seq)
    args.out.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    print(f"kept {len(kept)} lines -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
