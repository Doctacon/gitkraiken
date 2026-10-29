Status: open
Created: 2026-10-28
Updated: 2026-10-29

# Execute the approved world-class Rhizome roadmap

## Intent and current authorization

Execute the approved roadmap in `docs/design/world-class-rhizome.md` and its
validation plan. **Current scope is M1 only; stop before M2.** The operator asked
for remaining M1 work using engineering recommendations, then required every
increment to stay within 15 minutes. See `m1-completion.md` for the bounded work log.

Installer crash/retry remains explicitly deferred. No commits, publication,
private-provider writes, live user installation changes, invented human/platform
results, or M2 settings/keymap/theme/history/split-diff work. Public test interfaces
were confirmed earlier in `plan.md`. The earlier existing-checks-only choice applied
to the completed initial composer increment, not this M1 acceptance-closure work.

## Current state

- Current M1 baseline is the operator's `0a1ea66` (`feat: security pass`). All current
  changes remain uncommitted. Earlier roadmap work started at `70005ce`; its
  historical evidence is preserved in the linked artifacts below.
- Precision UI, narrow/wide layouts, plain rendering, successive-template composer,
  F4 core controls, worktree exclusion/reset/context restoration, conflict-aware
  draft saves, and captured-preview/no-clobber diagnostic export are integrated.
- Ordinary reviewed-index commits, terminal hooks/signing and supported Git actions
  remain available. Unproven destructive workflows remain refused; no recovery
  supervisor or universal Undo was added.
- A duplicate refresh scan was removed without removing post-fetch reconciliation.
  Remaining startup performance is profiled but still misses the approved warm
  startup target. No history-order change, persistent repository cache or target
  waiver was introduced.
- Current Git regression collection has 746 cases. Bounded partitions plus the
  additional CLI locale case cover 745 passes and the one deferred installer
  failure. This is aggregate local coverage, not one completely green full run.
- Dedicated composer tests pass on current Git and Git 2.36.0. Real 80×24 native
  commit journeys pass on both. Current-tmux startup, popup, hotkey and pane
  journeys pass in isolated UTF-8 environments. Fresh full minimum-tmux and
  cross-platform runs remain pending.
- M1-owned Python files were formatted with unchanged ASTs. Root lint and local
  wheel/sdist construction pass. The full formatter still flags untouched baseline
  files; no whole-tree formatter pass or hosted-CI success is claimed.

## Evidence and navigation

- `m1-completion.md`: current checklist, increment timings, commands/results and gaps.
- `m1-composer-validation.md`: exact current/minimum Git composer results.
- `m1-regression-*.txt`: complete bounded regression partitions and retained failures.
- `m1-terminal-*.txt`: isolated real-terminal results, including superseded failures.
- `m1-static-summary.txt`, `m1-build-local.txt`: static/build outcomes.
- `docs/validation/m1-performance.md`: full reference workload and unwaived misses.
- `docs/validation/m1-compatibility.md`: local versus pending environment matrix.
- `docs/validation/m1-usability.md`: real-user pilot plan, not fabricated results.

Historical execution detail remains in `evidence.md`, `commit-restoration.md`,
`composer-progress.md`, `fetch-root-evidence.md`, `refresh-race.md`, and the original
planning artifacts. Those reports describe their own candidates, not this final
local working tree.

## Outcome / remaining acceptance

M1 has substantial implemented and locally verified progress, but **M1 and release
acceptance are not declared complete**. Startup performance, pre-existing formatting
debt, the deferred installer defect, OS-cache-cold/resource/slow-provider evidence,
hosted and other-platform checks, and the real-user usability pilot remain open.
No M2 work has started.
