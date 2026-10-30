# Settings and Activity sizing follow-up

2026-10-30. Operator reported that both popups were far too small on a large
terminal. Work was split into Settings layout, Activity layout, shared control
geometry repairs, and bounded capture/regression increments.

## Changes

- Both frames use 90% of the terminal rather than fixed 70×24 / 76×22 caps.
  At 80×24 they use nearly the full screen with pinned, clickable action footers.
- Settings groups network/privacy and appearance/graph side by side on wide
  terminals, stacked below 110 columns. Scope comes first; explanatory text,
  settings paths, and errors share the scroll body instead of consuming its space.
- Activity gives the operation list and selected result a large side-by-side
  inspection area; narrow/short terminals stack them without wasting rows on
  enclosing borders. Closing still never cancels an operation.
- Select focus now styles SelectCurrent, avoiding a second outer border. Graph
  toolbar styles are scoped to the toolbar instead of also shrinking Settings'
  same-ID Select. Long selected labels fit; form actions align with their labels.
- Settings values, global offline lock behavior, operation selection, and Git
  guards are preserved. README was not changed by this follow-up.

## Verification

Final candidate: working tree over `2b211bf`, including the final keybinding-row
alignment and regression assertions. HEAD advanced externally during the work;
this session issued no commit or push.

**49 targeted tests passed in 147.06 seconds**:

```sh
uv run pytest tests/test_workspace_dialog_layout.py tests/test_operations_workspace.py tests/test_m1_visuals.py tests/test_m2_theme_dialogs.py tests/test_m1_controls.py tests/test_m2_settings.py tests/test_workspace_presentation.py -q
uv run ruff check .
uv run ruff format --check .
git diff --check
```

Ruff, whole-tree formatting (126 files), and whitespace checks passed. New checks
cover 270×80, 170×48, 100×32, and 80×24; screen/content geometry; actual footer
clicks; unclipped Select text; initial narrow content allocation; keyboard scroll
reachability; long paths/errors; resize/apply; and Activity selection/detail scroll
across resize and refresh. Existing global-offline, persistence, dark/high-contrast,
and real local stage/Activity regressions also pass.

[Final test output](dialog-layout/regression-final.txt). Earlier focus-border and
narrow-content/tab-budget failures remain under `dialog-layout/` for provenance;
these are superseded by the final green run.

## Reviewed renders

Actual app, disposable local demo repositories, isolated settings/state, no
external services. SVG originals sit beside these PNGs:

| Terminal/theme | Settings | Activity |
| --- | --- | --- |
| 270×80 light | [Settings](dialog-layout/settings-wide.png) | [Activity](dialog-layout/activity-wide.png) |
| 80×24 light | [Settings](dialog-layout/settings-narrow.png) | [Activity](dialog-layout/activity-narrow.png) |
| 170×48 dark | [Settings](dialog-layout/settings-dark.png) | [Activity](dialog-layout/activity-dark.png) |

Reproduce either view with the extended capture script:

```sh
uv run python scripts/capture_workspace.py /tmp/settings.svg --view settings --width 270 --height 80
uv run python scripts/capture_workspace.py /tmp/activity.svg --view activity --width 80 --height 24
```

These are headless rendered/interaction checks, not a real-user usability result,
real-terminal/platform certification, full backend regression, or release gate.
