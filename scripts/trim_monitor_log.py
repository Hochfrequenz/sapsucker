"""Trim a long ``sapsucker-monitor`` JSONL log to the samples the correlator can use.

The correlator reads only ``changed``, the values and the clock, so a log that is
mostly idle polling (``changed == []``) shrinks to a few dozen lines without
changing the timeline ``correlate`` produces. Kept:

- every header record (``record_type == "header"``, written by
  ``sapsucker-monitor --record``; it carries no ``seq``), in place;
- the baseline: the first *sample* in the file, whatever its ``seq``;
- every sample with a non-empty ``changed``, and the sample immediately before
  each of those (so a reader can see the before-state);
- the final in-range sample while a modal (``wnd[N]:Text``, N >= 1) is still
  open in it: the correlator ends such a dialog's bracket on the log's last
  sample, so dropping the idle tail would shorten the bracket.

``--through-seq`` applies to samples only. The guarantee is that ``correlate``
gives the same timeline on the output as on the input cut at ``--through-seq``.
Original ``seq`` and ``elapsed_s`` values are preserved. Empty input gives empty
output.

Usage::

    uv run python scripts/trim_monitor_log.py FULL.jsonl OUT.jsonl --through-seq 197
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_MODAL_TEXT = re.compile(r"wnd\[([1-9]\d*)\]:Text")
_SENTINELS = frozenset({"<unreadable>", "<absent>"})


def _modal_open(sample: dict[str, Any]) -> bool:
    # Same presence test as the correlator's _modal_brackets.
    return any(_MODAL_TEXT.fullmatch(k) and isinstance(v, str) and v not in _SENTINELS for k, v in sample.items())


def trim(lines: list[str], through_seq: int | None) -> list[str]:
    """Return the kept lines, in order. Blank lines are ignored."""
    rows = [(line.rstrip("\n"), json.loads(line)) for line in lines if line.strip()]
    keep: set[int] = set()
    samples: list[int] = []  # row indices of in-range samples, in order
    for i, (_, d) in enumerate(rows):
        if d.get("record_type") == "header":
            keep.add(i)
            continue
        if through_seq is not None and d["seq"] > through_seq:
            break
        if not samples:
            keep.add(i)  # the baseline
        elif d.get("changed"):
            keep.add(i)
            keep.add(samples[-1])
        samples.append(i)
    if samples and _modal_open(rows[samples[-1]][1]):
        keep.add(samples[-1])
    return [rows[i][0] for i in sorted(keep)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--through-seq", type=int, default=None, help="drop every sample after this seq")
    args = ap.parse_args(argv)
    kept = trim(args.source.read_text(encoding="utf-8").splitlines(), args.through_seq)
    args.out.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    print(f"kept {len(kept)} lines -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
