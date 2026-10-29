Status: open
Created: 2026-10-28
Updated: 2026-10-29

# Execute the approved world-class Rhizome roadmap

## Intent and current authorization

Execute the approved roadmap in `docs/design/world-class-rhizome.md` and its
validation plan. **Current scope is M1 only; stop before M2.** The operator asked
for remaining M1 work using engineering recommendations, then required every
increment to stay within 15 minutes. See `m1-completion.md` for the bounded work log.

The operator subsequently authorized the installer crash/retry repair, retaining
the 15-minute increment limit. No commits, publication,
private-provider writes, live user installation changes, invented human/platform
results, or M2 settings/keymap/theme/history/split-diff work. Public test interfaces
were confirmed earlier in `plan.md`. The earlier existing-checks-only choice applied
to the completed initial composer increment, not this M1 acceptance-closure work.

## Current state

- The formatting follow-up starts from clean commit `ea6df96` (`feat: M1`).
  Earlier M1 work used `0a1ea66` plus working-tree changes; earlier roadmap work
  started at `70005ce`. Historical evidence retains those original identities.
  Formatting and installer-repair follow-ups remain uncommitted.
- Precision UI, narrow/wide layouts, plain rendering, successive-template composer,
  F4 core controls, worktree exclusion/reset/context restoration, conflict-aware
  draft saves, and captured-preview/no-clobber diagnostic export are integrated.
- Ordinary reviewed-index commits, terminal hooks/signing and supported Git actions
  remain available. Unproven destructive workflows remain refused; no recovery
  supervisor or universal Undo was added.
- A duplicate refresh scan was removed without removing post-fetch reconciliation.
  On 2026-10-29 the operator accepted the measured 1.509 s startup for this M1
  candidate. It is no longer an M1 blocker; this does not waive other performance
  evidence or establish a new general threshold. No history-order change or
  persistent repository cache was introduced.
- The earlier 746-case bounded regression evidence covered 745 passes and one
  installer failure. That failure is now repaired: all 37 installer cases and
  eight CLI/distribution cases pass together. A new complete-suite run has not
  been performed; historical partition evidence remains explicitly scoped.
- Dedicated composer tests pass on current Git and Git 2.36.0. Real 80×24 native
  commit journeys pass on both. Current-tmux startup, popup, hotkey and pane
  journeys pass in isolated UTF-8 environments. Fresh full minimum-tmux and
  cross-platform runs remain pending.
- The operator authorized clearing the remaining 21-file formatting debt. All 21
  files now match Ruff with identical before/after ASTs. Root lint, whole-tree
  formatting and whitespace checks pass. Python test files received formatting
  only. The subsequent installer repair changes `scripts/install.sh` to retain
  its activity fence after signal-style exits, without automatic recovery claims.
  Prior local packaging checks pass; hosted-CI success is not claimed.

## Evidence and navigation

- `m1-completion.md`: current checklist, increment timings, commands/results and gaps.
- `m1-composer-validation.md`: exact current/minimum Git composer results.
- `m1-regression-*.txt`: complete bounded regression partitions and retained failures.
- `m1-terminal-*.txt`: isolated real-terminal results, including superseded failures.
- `m1-static-summary.txt`, `m1-build-local.txt`: earlier static/build outcomes.
- `m1-formatting-followup.json`, `m1-formatting-tests.txt`: current 21-file formatting
  proof and affected-test results.
- `m1-installer-repair.md`: crash/retry cause, isolated verification and limits.
- `docs/validation/m1-performance.md`: full reference workload and startup acceptance.
- `docs/validation/m1-compatibility.md`: local versus pending environment matrix.
- `docs/validation/m1-usability.md`: real-user pilot plan, not fabricated results.

Historical execution detail remains in `evidence.md`, `commit-restoration.md`,
`composer-progress.md`, `fetch-root-evidence.md`, `refresh-race.md`, and the original
planning artifacts. Those reports describe their own candidates, not this final
local working tree.

## Outcome / remaining acceptance

M1 has substantial implemented and locally verified progress, but **M1 and release
acceptance are not declared complete**. Startup timing is accepted and formatting
debt is cleared. The installer crash/retry regression passes after repair.
Live installer validation, OS-cache-cold/resource/slow-provider evidence, hosted
and other-platform checks, and the real-user usability pilot remain open.
No M2 work has started.
