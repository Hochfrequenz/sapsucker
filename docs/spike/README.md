# docs/spike

Recordings and monitor logs from the recording-to-skill spike. The `.vbs` files
are SAP GUI Scripting recordings; the `.jsonl` files are `sapsucker-monitor`
logs that `sapsucker-correlate` pairs them with.

## `journey6_bp_timing.trimmed.jsonl`

The fixture behind `TestJourney6Acceptance` and `TestJourney6Modal` in
`unittests/test_correlate.py`, so the correlator is checked against a real log
in CI rather than only against hand-built ones.

- **Source:** `git show 3581a44^:journey6_bp_timing.jsonl` (4134 samples),
  recorded 2026-08-26, monitor v2 schema.
- **Trim rule** (`scripts/trim_monitor_log.py`, run with `--through-seq 197`):
  keep any header record (`sapsucker-monitor --record` writes one; this v2 log
  has none), the first sample (the baseline), every sample with a non-empty
  `changed`, the sample just before each of those, and the final in-range
  sample if a modal (`wnd[N]:Text`, N >= 1) is still open in it, since the
  correlator ends such a dialog's bracket on the last sample. `--through-seq`
  cuts samples only. Original `seq` and `elapsed_s` are unchanged. The dropped
  tail (seq 198 onward; the samples from seq 2685 are an unrelated SE16N
  excursion ten minutes after the journey) is outside the cutoff. The modal is
  closed at seq 197, so the last rule adds nothing here: re-trimming the source
  gives the committed file byte for byte.
- **Checked:** `correlate` on the log cut at seq 197, on the whole log, and on
  the trimmed log produces the same steps (strategy, `t_start`, `t_end`, flags,
  status-bar text) and the same markdown, for `journey3_bp.vbs`.
- **Content:** element ids, window titles, screen numbers and wall-clock `at`
  stamps. No entered field values are sampled, but window titles can carry
  data: one sample's `wnd[0]:Text` is `Person anzeigen: 3961`, the
  business-partner number the journey created on the test system. The tests
  assert only on the titles `Person anlegen` and `Warnung`.
- **No status-bar data.** This log has no `sbar_*` keys (it predates #127), so
  the status-bar attribution is covered by synthetic tests only. A real run
  needs a human with SAP GUI:

  ```powershell
  git fetch origin
  git checkout <branch>
  git pull
  uv sync --group dev
  uv run sapsucker-monitor --record se16nsbar.vbs --interval 0.2 --out journey_se16n_sbar.jsonl
  # then play docs/spike/journey_se16n.vbs by hand in the recorder; stop the monitor; attach both files.
  ```

  The `--record` name must be ASCII letters and digits with at most one dot
  (SAP GUI's `RecordFile` rejects `_`, `-` and spaces, and the monitor exits
  with `bad --record` before attaching); the `--out` name is unrestricted.

  The JSONL must contain `sbar_text` with at least one non-empty value after the
  execute (`sendVKey 8`); otherwise the run is useless for this purpose.

Git ignores `*.jsonl`; `.gitignore` re-includes `docs/spike/*.trimmed.jsonl`,
so this file needs no `git add -f`. `journey5_timing.jsonl` predates that rule
and was force-added.
