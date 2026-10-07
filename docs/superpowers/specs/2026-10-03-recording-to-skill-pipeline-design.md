# Recording → automation artefact: phased plan

Status: PROPOSED (scope agreed 2026-10-03; small steps, each builds on the last).
Everything here lives in sapsucker. The pipeline's *output* is "a merged,
timestamped journey document"; what consumes it (e.g. skill generation) is out
of scope for the whole plan — see "Later".

## Goal

A human does a task in SAP GUI once, talking while they work. From that one
pass we produce, with minimal manual steps and no third-party recorder:

1. the built-in recorder's `.vbs` (what was done, exact element ids),
2. the monitor's JSONL (when, and what the screen showed),
3. an optional narration transcript (why),
4. **one merged timeline** joining all three — the artefact an LLM (or a human)
   can read once to author a parameterized automation without three-way guessing.

## Evidence this is buildable (verified on real data)

- **The VBS parses.** `docs/spike/journey3_bp.vbs` (18 statements): every
  statement classified by a ~15-line matcher after skipping the 14-line
  preamble. Statement forms in the committed corpus: **property assignment**
  (`.text = "…"`, `.key = "0002"`), **bare no-argument method** (`.press`,
  `.setFocus`), and **VB-style unparenthesised arguments** (`.sendVKey 0`,
  `.resizeWorkingPane 152,33,false`). No parenthesised member invocation
  appears in this recording — the grammar still accepts one
  (`findById(…).m(args)`), the corpus just never exercises it. VB string
  escaping is only `""`. Known silent-failure risk of pure regex
  (colon-chained statements, line continuations) → the parser is a small
  hand-written tokenizer with a **round-trip guardrail** on the statement
  region (see Phase 1 for its exact shape); any unknown construct fails loudly
  with line number, never silently skips.
- **The correlation works.** Prototype scored against journey 3's `.vbs` paired
  with the journey-6 JSONL (same task family): **17/18 recorded steps
  timestamped** — 10 exact focus matches, 3 by DDIC-field suffix
  (`*-TEL_NUMBER` survives the journey-3-vs-6 layout variance, #82 Finding 3),
  2 popup presses bracketed by the modal's open/close samples, 1 SendVKey bound
  to the transaction change, and the un-narratable `btn[5]` pinned by #82
  Finding 4's fingerprint (title changes, screen number doesn't). Only
  `resizeWorkingPane` (line-1 recorder boilerplate) has no timestamp, by design.
  (CI-checked by `TestJourney6Acceptance` against the trimmed fixture
  `docs/spike/journey6_bp_timing.trimmed.jsonl`.) **Not verified:** that a narration
  transcript starts at the recorder start (the `recorder_skew` shift assumes it), and
  status-bar attribution against a real log (synthetic tests only); see the
  Known limits in `src/sapsucker/_correlate.py`.
- **The corpus survives on this machine.** The recorder's configured output
  folder (registry `HKCU\...\Scripting\SaveScriptTo`) holds journey3/4/5/6,
  `journey_se16n` and `se16but000` (UTF-16). `se16but000.vbs` is confirmed as
  journey 2 (its ten `firstVisibleRow` assignments are exactly #82's
  `1, 8, 17, 26, 35, 44, 70, 78, 133, 470`). journey6's 4134-sample JSONL pairs
  with `journey6_bp.vbs`. Whether `journey_se16n.vbs` is #100's repeat run with
  the `--watch` output is **unverified** — Phase 0 validates it before any
  claim lands on #100.
- **Value over plain recording:** plain `.vbs` replays (proven in #82), but
  provides no timestamps, no outcomes, and loses the editing process. The
  monitor adds all three (Findings 1/2/5). The merged timeline is what makes
  artefact authoring cheap: one deterministic document instead of inference.

## Phases (each independently mergeable and useful)

### Phase 0 — corpus resurrection + #100 closure *(docs-only, tiny)*

- Copy the surviving recordings from the recorder folder into `docs/spike/`
  (UTF-16 → UTF-8, mask personal values per #82's rule, header comment stating
  what was redacted).
- Validate identities: `se16but000.vbs` vs #82's journey-2 scroll list (already
  matched); `journey_se16n.vbs` vs the repeat-run description; the recorder
  folder's JSONL candidates against #100's six-and-four figures.
- Comment on #100 with what is now proven (and correct or downgrade
  `monitor.py`'s docstring figures accordingly).

### Phase 1 — VBS parser *(small, pure library code)*

`sapsucker/_recording.py` (name TBD): tokenizer + document model.

- **Document model preserves the file bytes**: encoding/BOM, line endings, the
  header comments and the guarded preamble are kept verbatim;
  `steps` is a parsed view over the statement region.
- **Round-trip guardrail** over every committed corpus file: re-rendering the
  document model must reproduce the original bytes. The *parsed statements*
  additionally round-trip against their statement region (normalised). Any
  unknown construct → `RecordingParseError` with line number and text.
- Grammar (superset of what the corpus exercises): `findById(STRING)` `.`
  MEMBER, then one of `=` EXPR | `(` ARGS `)` | bare args | nothing; VB string
  rules (`""` escape, strings may contain anything).
- Unit tests: all corpus files parse and round-trip; malformed inputs fail
  loudly; each statement form (including parenthesised invocation, not present
  in the corpus); UTF-16 and UTF-8 inputs.

### Phase 2 — member-gap check *(completes #100's "worth doing separately")*

Script (CLI or pytest helper): parse a recording, diff its `(member,
element-type)` pairs against what sapsucker wraps. Mapping needs more than
`PREFIX_TO_TYPE_NAME` (`src/sapsucker/_types.py`):

- **Structural type refinement**: `wnd[0]` is `GuiMainWindow` (which wraps
  `resize_working_pane`), modals are `GuiFrameWindow` — the prefix alone says
  `GuiFrameWindow` for both and would report false gaps.
- **COM-name → Python-name mapping** for the translation caveats #82 records
  (`GetTreeType` property, `PressF1` → `press_f1`, `Required` → `is_required`,
  `ModifyCell` reachable twice), plus inherited members.
- Output: per-recording report of genuinely unmapped members. Run in CI over
  the committed corpus so a new recording surfaces gaps automatically.

### Phase 3 — status-bar sampling in the monitor *(the outcome signal)*

- `read_once` gains `sbar_type` / `sbar_id` / `sbar_number` / `sbar_text`
  (wrapper exists since #116); `--no-statusbar` opt-out.
- Measure achieved sampling period before/after (docstring documents it as
  interval + read time).
- Re-run the journey-6 style capture: the JSONL then shows the save outcome
  (title change plus the sbar message) that Finding 5 could only infer from
  the title.

### Phase 4 — `RecordFile` wrapper + one-command capture *(kills manual pairing)*

- Wrap `ISapSessionTarget.Record` / `RecordFile` (both confirmed in the
  typelib dump) on `GuiSession`; surface "recording disabled" clearly.
- **Acceptance is live, not fake-based**: a run against a real SAP GUI must
  produce a non-empty recording at the requested path, and the
  recording-disabled failure mode must be provoked (or explicitly reported as
  unprovokeable) with the observed diagnostic recorded. The typelib proves the
  members exist, not the setter sequence's behaviour.
- `sapsucker-monitor --record <name>`: starts the COM recorder and the sampler
  from one process. The two calls are sequential, not simultaneous — so the
  tool records a **shared monotonic origin** taken before the recorder start
  and the skew (recorder-start minus sampler-origin) into the JSONL header /
  first sample, instead of claiming `elapsed: 0` equals the recording start.
  (The current sampler computes its first `elapsed` only after the first
  `read_once`, so its first record is not zero — that origin handling is part
  of this phase.) #95 item 3's manual alignment then reduces to a measured
  constant skew instead of "start both by hand and hope".
- Every future capture yields a `.vbs` + JSONL pair for free, growing the
  parser/correlator corpus as a by-product.

### Phase 5 — the correlator *(the payoff; prototype already scored 17/18)*

`sapsucker.correlate` (or scripts/): merged timeline as JSONL + markdown.

Matching strategies, in order (each falls back to the next):

1. **Exact focus match** — recorded id's last path segment == a changed
   sample's `focus_id` segment → step timestamped at that sample.
2. **DDIC-suffix match** — match on the field name after `-` (e.g.
   `TEL_NUMBER`): survives the same-human-different-subtree variance (#82
   Finding 3) and flags it explicitly as a *layout-sensitive* step.
3. **Modal bracketing** — `wnd[1]` presses get the modal's open→close window
   from `wnd[1]:Text` presence.
4. **Screen fingerprints** — SendVKey/press on `wnd[0]` bound to the next
   `transaction`/`screen_number` change; un-narratable button presses to
   title-change-with-stable-screen-number events (Finding 4's fingerprint).
5. **Boilerplate table** — `resizeWorkingPane` and friends are labelled
   `recorder-boilerplate, t≈0` rather than matched.

Output per step: element id, member, args, matched window `[t_start, t_end]`,
strategy + confidence, status-bar text in that window (phase 3),
transcript excerpts (word timestamps from the STT output intersected with the
window), and the flags that matter to a downstream author
(layout-sensitive match, modal count ambiguity, sub-interval fast repeats).

Transcript input: any word-timestamped format (SRT / whisper JSON) — decide at
implementation time; the correlator takes a normalized
`[(t_start, t_end, text)]` stream so STT choice stays out of scope.

### Later (out of scope for this whole plan)

- Skill generation itself (structure from narration, scripts from the parsed
  VBS with PARAMS factoring, existence-check injection): a consuming project.
- FLP desktop→browser hand-off capture: separate effort, tracked in sapgui.mcp.

## Risks / open questions

- **Sub-interval actions collapse** into one sample (known limit): the timeline
  gives a *window*, not an instant; consecutive same-field steps share it.
  Acceptable for authoring; flagged per step.
- **UTF-16 corpus files** must be transcoded carefully (mask first, then
  encode); the parser accepts both encodings.
- **Status-bar cost** on the sampling period is unknown until phase 3 measures
  it; the opt-out is the escape hatch.
- The correlator's confidence values need a human eyeball on the merged
  timeline once before we trust the scoring — phase 5's acceptance test is
  "the journey-6 timeline reads as a plausible description of the task to
  someone who wasn't there".
