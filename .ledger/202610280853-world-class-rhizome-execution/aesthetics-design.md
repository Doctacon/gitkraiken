# Aesthetics and UX pass

## Direction and scope

The operator made aesthetics and UX the highest priority after reviewing the live
workspace. This is a shipped-UI iteration in the working tree, not a mockup or a
claim of M2 acceptance. No Git transaction, checkout, publication, or network safety
guard was removed. No commit/push was performed.

- One identity/action band instead of a separate repository header and toolbar.
  History, Changes, Commit and Refs are explicit tasks; Activity and Settings are
  secondary utilities. Branch creation, stash and Pull remain in Actions.
- Commit subjects precede branches and metadata. Metadata columns progressively
  appear with available space; full values remain in the inspector. Default
  column budgets reserve scrollbar width and retain every graph lane. Explicit
  user column widths and horizontal scrolling still work.
- Medium terminals (85–119 columns) prioritize history plus inspection; Refs can
  summon the rail. Below 85 columns one task owns the canvas. Enter on a commit
  explicitly opens inspection; Esc returns. History exits a diff; Esc preserves
  the existing return-to-review behavior.
- Neutral light surfaces, compatible dark/high-contrast themes, restrained native
  pointer marks instead of emoji, and quieter resize/scroll affordances. Legacy
  Rich colors still translate correctly into every curated palette.
- Cleaner reference/file trees without ASCII guide clutter. Worktrees show a
  short name while retaining exact targets and full-path filtering. Long labels
  and diagnostics remain horizontally scrollable (hover popups remain disabled).
- Commits with more than 20 changed files start with counted, folded top-level
  directory groups. Explicit expansion/selection survives refresh. Small commits
  retain the existing expanded view.
- Diff inspection reuses its file toolbar instead of nesting a second border and
  heading. Source lines, exact-selection status, and staging controls are unchanged.

## Integration findings

Visual inspection found clipped search labels and an oversized history scrollbar;
label widths and rail budgeting were corrected rather than hiding the data.
An initial cropping-only sidebar implementation was rejected because it prevented
reading long diagnostics; horizontal scrolling was restored with quiet styling.

The medium-width right-click loop exposed a genuine focus/layout defect: the
application's generic focused-input border enlarged the one-line reference filter.
On MouseDown, focus moved into the tree, the filter shrank, and the tree moved from
y=10 to y=9; Click then targeted the next branch. A stable, borderless `#ref-filter`
with a focus background fixes this. Both the real context-menu copy test and a
new focus-region invariant cover it. Temporary instrumentation was removed.

## Visual evidence and reproduction

Current local-only, isolated-home captures are in `docs/assets/`:

- `workspace-light.png` — 240×48 history
- `workspace-dark.png` — 170×42 history
- `workspace-80x24.png` — narrow history
- `workspace-diff-80x24.png` — exact review controls without redundant framing
- `workspace-composer-80x24.png` — composer and draft state

Light/dark/high-contrast plus 80×24 and medium layouts were inspected via generated
PNG renders, not inferred from assertions. These are Textual headless renders,
not proof of terminal/font/platform behavior or a human usability study.

Reproduce without using real application state or external remotes:

```sh
uv run python scripts/capture_workspace.py /tmp/history.svg --width 240 --height 48
uv run python scripts/capture_workspace.py /tmp/dark.svg --theme dark
uv run python scripts/capture_workspace.py /tmp/review.svg --view diff --width 80 --height 24
```

## Validation

`aesthetics-validation-manifest.json` partitions **242 collected tests** exactly
once across three bounded commands. All pass:

- `aesthetics-regression-1.txt`: **42 passed**, 229.68 s
- `aesthetics-regression-2.txt`: **42 passed**, 248.63 s
- `aesthetics-regression-3.txt`: **158 passed**, 418.23 s

The earlier 128-pass/9-failure run is retained as
`aesthetics-initial-regression.txt`; presentation assumptions and the actual focus/
scroll defects it exposed were corrected before the final full partitions ran.
This covers application interactions, palettes, responsive layouts, reference
identity, context menus, resizing, commit file trees, precision/split diffs,
settings/state, investigation, and refresh. It is not the full backend/install suite.

Root Ruff lint, whole-tree format and whitespace checks pass. README and usage
now describe the current navigation and link the current captures. Historical M1
capture assets remain unchanged.
