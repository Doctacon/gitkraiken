# V03 prototype evidence

**Scope:** throwaway Textual prototype only. Read design inputs: `world-class-rhizome.md` C1–C3/C8 and M1; `validation-plan.md` V03; existing `src/rhizome/app.py` and `src/rhizome/app.tcss`. The existing app's quiet light palette, top repository context, familiar three-pane workspace, inspector composer, teal primary actions, and constrained-pane precedent informed the sketch. No production file was edited.

## Artifacts

| Variant | 80×24 SVG | 170×48 SVG |
| --- | --- | --- |
| A | `evidence/variant-a-80x24.svg` | `evidence/variant-a-170x48.svg` |
| B | `evidence/variant-b-80x24.svg` | `evidence/variant-b-170x48.svg` |
| C | `evidence/variant-c-80x24.svg` | `evidence/variant-c-170x48.svg` |

Textual exported the requested 80×24 snapshots with SVG viewBox `994×635.6` and 170×48 with `2092×1221.2`. `evidence/interaction-record.json` records the two headless scripted journeys.

## Actually exercised

- `uv run --locked python -m py_compile …` completed for the prototype and capture/recording utilities.
- Textual's headless renderer mounted all three variants and exported all six requested SVGs.
- At both declared sizes, a headless scripted journey invoked keyboard mark → stage → unstage; invoked mouse controls for mark/stage/change/back/variant; exercised the stale notice/refusal path; and preserved the seeded draft string.
- The record shows variant cycling remained **B** while the composer was focused, then moved to **C** once focus moved to the diff table. This is the prototype's guard against stealing text entry.

## Overflow, focus, and interaction limits

- **Measured viewports:** 80×24 and 170×48 only. At 80×24 the CSS explicitly exposes one primary panel and hides the other two until the visible Files/Commit summons are used; at 170×48 all A/C panes and B's three stacked regions are rendered. No clipped-region or scroll-extent metric was collected.
- **Focus:** headless focus shows the TextArea guard for the variant shortcut. It does not establish terminal focus-ring visibility, screen-reader behavior, mouse coordinate fidelity, selection behavior in tmux/SSH, or real copy/paste behavior.
- **Interaction:** the prototype uses a DataTable cursor plus `v` anchor; it does not implement drag selection or Shift-range selection. Mouse row selection was mounted but not separately recorded as an exact coordinate/gesture assertion. The script's 80×24 stale path left the simulated stage flag true while the wide path left it false; this is a headless layout/click-coordinate limitation, not evidence of staging correctness. V01 real-Git semantics are absent.
- **Accessibility:** non-color state text, explicit labels, and keyboard equivalents are included, but no accessibility claim is made. Textual SVG/headless evidence is not real-terminal or user acceptance.
- The requested prototype skill/UI reference was not present in this repository's discoverable skill paths; the actual Textual app and stylesheet were used as the UI reference instead.

## Explicitly not exercised

No real terminal session, human pilot, real/disposable Git repository, index/worktree mutation, hook, persistence/reopen, performance measurement, platform matrix, tmux/SSH, screen reader, or mouse hardware session was run. No human-pilot claim is made.
