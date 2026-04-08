"""End-to-end benchmark of GuiVContainer.dump_tree on a real SAP screen.

Compares the new fast path (GetObjectTree + JSON parse) against the old
per-property COM-read path. Run with the dev install and a working SAP
environment:

    python scripts/benchmark_dump_tree.py

Reports per-call duration_ms and the speedup ratio. Output is captured
to scripts/benchmark_output.txt.

Validates the issue #20 acceptance criterion: "On a representative
~200-element screen, dump_tree wall-clock time drops by at least 5x".
"""
# pylint: skip-file

from __future__ import annotations

import logging
import statistics
import sys
import time
from pathlib import Path

# Enable INFO logging from sapsucker so we can see which path dump_tree took.
# (and DEBUG so we see the fallback reason if the fast path fails)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-7s %(name)-40s %(message)s  %(extra_str)s",
)


# Custom formatter to show the `extra=` dict alongside the message
class _ExtraFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # Pull "extra" fields by diffing record attrs against the standard set
        std = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime", "extra_str",
            "taskName",
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in std}
        record.extra_str = " ".join(f"{k}={v!r}" for k, v in extras.items()) if extras else ""
        return super().format(record)


for h in logging.getLogger().handlers:
    h.setFormatter(_ExtraFormatter("%(asctime)s %(levelname)-7s %(name)-40s %(message)s  %(extra_str)s"))

# Make sure we use the dev install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # type: ignore[import-not-found]

sys.path.insert(0, "C:/github/sapwebgui.mcp/src")
load_dotenv("C:/github/sapwebgui.mcp/.env")

from sapsucker.components.base import _dump_tree_recursive  # noqa: E402
from sapsucker.login import login as sapsucker_login  # noqa: E402


def main() -> int:
    from sapwebguimcp.models.config import get_sap_config

    sap_cfg = get_sap_config()
    system = sap_cfg.get_default()

    print("Logging in...")
    session = sapsucker_login(
        connection_name=system.connection_name,
        client=system.client,
        user=system.user,
        password=system.password.get_secret_value(),
        language=system.language,
    )
    print(f"Session: {session.id}")

    # Navigate to a busy screen
    print("Navigating /nBP F5 (Person create)...")
    okcd = session.find_by_id("wnd[0]/tbar[0]/okcd")
    okcd.text = "/nBP"
    session.find_by_id("wnd[0]").send_v_key(0)
    time.sleep(2)
    session.find_by_id("wnd[0]").send_v_key(5)  # F5
    time.sleep(2)
    try:
        wnd1 = session.find_by_id("wnd[1]", raise_error=False)
        if wnd1 is not None:
            wnd1.send_v_key(0)
            time.sleep(1)
    except Exception:
        pass

    # The wnd[0] container — what we'll dump
    wnd = session.find_by_id("wnd[0]")
    print(f"Dumping {wnd.id} ({type(wnd).__name__})")

    # Slow path: per-property reads via _dump_tree_recursive directly
    # (bypasses dump_tree's fast-path attempt)
    print("\nMeasuring SLOW path (per-property COM reads, _dump_tree_recursive)...")
    slow_durations = []
    for i in range(3):
        start = time.perf_counter()
        result_slow = _dump_tree_recursive(wnd.com, 0, 200)
        dur = (time.perf_counter() - start) * 1000
        slow_durations.append(dur)
        print(f"  run {i + 1}: {dur:.1f} ms ({_count(result_slow)} elements)")

    slow_count = _count(result_slow)

    # Fast path: GuiVContainer.dump_tree (which now tries fast path first)
    print("\nMeasuring FAST path (GuiVContainer.dump_tree, GetObjectTree fast path)...")
    fast_durations = []
    for i in range(3):
        start = time.perf_counter()
        result_fast = wnd.dump_tree()
        dur = (time.perf_counter() - start) * 1000
        fast_durations.append(dur)
        print(f"  run {i + 1}: {dur:.1f} ms ({_count(result_fast)} elements)")

    fast_count = _count(result_fast)

    # Summary
    slow_median = statistics.median(slow_durations)
    fast_median = statistics.median(fast_durations)
    speedup = slow_median / fast_median if fast_median else 0

    print("\n=== RESULTS ===")
    print(f"  Element count slow: {slow_count}")
    print(f"  Element count fast: {fast_count}")
    print(f"  Slow median: {slow_median:.1f} ms")
    print(f"  Fast median: {fast_median:.1f} ms")
    print(f"  Speedup:     {speedup:.1f}x")
    print(f"  Acceptance criterion (>=5x): {'PASS' if speedup >= 5 else 'FAIL'}")

    # Cleanup
    try:
        for _ in range(5):
            session.find_by_id("wnd[0]").send_v_key(3)
            time.sleep(0.3)
    except Exception:
        pass

    return 0 if speedup >= 5 else 1


def _count(elems) -> int:
    n = 0
    for e in elems:
        n += 1 + _count(e.children)
    return n


if __name__ == "__main__":
    sys.exit(main())
