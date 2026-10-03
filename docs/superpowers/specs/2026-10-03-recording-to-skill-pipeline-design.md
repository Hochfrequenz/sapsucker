# Recording → automation artefact: phased plan

Status: PROPOSED (scope agreed 2026-10-03; small steps, each builds on the last).
Everything here lives in sapsucker; nothing yet publishes or assumes the internal
skill-repo layout — the pipeline's *output* is "a merged, timestamped journey
document"; what consumes it is out of scope for phases 0–3.

## Goal

A human does a task in SAP GUI once, talking while they work. From that one
pass we produce, with minimal manual steps and no third-party recorder:

1. the built-in recorder's `.vbs` (what was done, exact element ids),
2. the monitor's JSONL (when, and what the screen showed),
3. an optional narration transcript (why),
4. **one merged timeline** joining all three — the artefact an LLM (or a human)
   can read once to author a parameterized automation without three-way guessing.

## Evidence this is buildable (all verified on real data)

- **The VBS parses.** `docs/spike/journey3_bp.vbs` (18 statements): every
  statement classified by a ~15-line matcher after skipping the 14-line
  preamble — three statement forms (property assignment, bare-arg method,
  paren method), VB string escaping is only `""`. Known silent-failure risk of
  pure regex (colon-chained statements, line continuations) → the parser is a
  small hand-written tokenizer with a **round-trip guardrail** (re-render must
  byte-match the input file; any unknown construct fails loudly with line
  number, never silently skips).
- **The correlation works.** Prototype scored against journey 3's `.vbs` paired
  with the journey-6 JSONL (same task family): **17/18 recorded steps
  timestamped** — 10 exact focus matches, 3 by DDIC-field suffix
  (`*-TEL_NUMBER` survives the journey-3-vs-6 layout variance, #82 Finding 3),
  2 popup presses bracketed by the modal's open/close samples, 1 SendVKey bound
  to the transaction change, and the un-narratable `btn[5]` pinned by #82
  Finding 4's fingerprint (title changes, screen number doesn't). Only
  `resizeWorkingPane` (line-1 recorder boilerplate) has no timestamp, by design.
- **The corpus exists.** The recorder's actual output folder (registry
  `HKCU\...\Scripting\SaveScriptTo`) holds journey3/4/5/6, `journey_se16n` and
  `se16but000` (UTF-16; journey 2's ten `firstVisibleRow` assignments confirmed
  in it — this also answers issue #100's "are the recordings still on the
  box?" — yes). journey6's 4134-sample JSONL pairs with `journey6_bp.vbs`.
- **Value over plain recording:** plain `.vbs` replays (proven in #82), but
  provides no timestamps, no outcomes, and loses the editing process. The
  monitor adds all three (Findings 1/2/5). The merged timeline is what makes
  artefact authoring cheap: one deterministic document instead of inference.

## Phases (each independently mergeable and useful)

### Phase 0 — corpus resurrection + #100 closure *(docs-only, tiny)*

- Copy the surviving recordings from the recorder folder into `docs/spike/`
  (UTF-16 → UTF-8, mask personal values per #82's rule, header comment stating
  what was redacted).
- Comment on #100: the two "missing" SE16N recordings are found
  (`se16but000.vbs` = journey 2's ten scrolls; `journey_se16n.vbs` likely the
  repeat run); fix `monitor.py`'s docstring if the six-and-four figures become
  reproducible from the found JSONL, else downgrade the wording as #100 asks.

### Phase 1 — VBS parser *(small, pure library code)*

`sapsucker/_recording.py` (name TBD): tokenizer + statement model.

- Skips the guarded preamble (assert its shape rather than regex-free-hand it).
- Statement = `findById(STRING)` `.` MEMBER (`=` EXPR | `(ARGS)` | BARE-ARGS),
  VB string rules (`""` escape, strings may contain anything).
- Unknown statement → `RecordingParseError` with line number and text.
- **Round-trip guardrail** as a unit test over every committed corpus file:
  render(parsed) == original bytes.
- Output model: ordered `RecordingStep(element_id, member, args, raw_line,
  line_no)`.
- Unit tests: all corpus files parse; malformed inputs fail loudly; the three
  statement forms; UTF-16 and UTF-8 inputs.

### Phase 2 — member-gap check *(completes #100's "worth doing separately")*

Script (CLI or pytest helper): parse a recording, diff its
`(member, id-prefix)` pairs against what sapsucker wraps
(`PREFIX_TO_TYPE_NAME` from `src/sapsucker/_types.py` for the type; wrapper
presence per component class). Output: per-recording report of unmapped
members. Run it in CI over the committed corpus so a new recording surfaces
gaps automatically.

### Phase 3 — status-bar sampling in the monitor *(the outcome signal)*

- `read_once` gains `sbar_type` / `sbar_id` / `sbar_number` / `sbar_text`
  (wrapper exists since #116); `--no-statusbar` opt-out.
- Measure achieved sampling period before/after (docstring documents it as
  interval + read time).
- Re-run the journey-6 style capture: the JSONL then shows the save outcome
  ("Person anzeigen: <nr>" title change plus the sbar message) that Finding 5
  could only infer from the title.

### Phase 4 — `RecordFile` wrapper + one-command capture *(kills manual pairing)*

- Wrap `ISapSessionTarget.Record` / `RecordFile` (both confirmed in the
  typelib dump) on `GuiSession`; surface "recording disabled" clearly.
- `sapsucker-monitor --record <name>`: starts the COM recorder and the sampler
  in one instant — `elapsed: 0` *is* the recording start; #95 item 3's manual
  alignment problem disappears.
- Every future capture then yields a `.vbs` + JSONL pair for free, growing the
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

### Later (not in these phases)

- Skill generation itself (structure from narration, scripts from the parsed
  VBS with PARAMS factoring, existence-check injection): a consuming project,
  out of sapsucker scope.
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
