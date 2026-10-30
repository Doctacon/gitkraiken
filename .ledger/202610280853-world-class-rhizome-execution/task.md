Status: open
Created: 2026-10-28
Updated: 2026-10-30

# Execute the approved world-class Rhizome roadmap

## Intent and current authorization

Execute the approved roadmap in `docs/design/world-class-rhizome.md` and its
validation plan. **Current scope is M2**, newly authorized on 2026-10-29 after M1
acceptance. Every individual piece must take 15 minutes or less; split an oversized
piece repeatedly until it fits. See `m2-execution.md` for current decomposition and
evidence, and `m1-completion.md` for the completed M1 work log.

During M1, the operator also authorized installer repair and release validation,
retaining the 15-minute increment limit. That phase's direction was to finish a
**full M1 release**, not unilaterally narrow it to a preview. Linux lifecycle
validation has now been completed through Docker's copy API. The operator then
explicitly selected “Accept full M1 readiness,” deferring the named unrun
platform/human/performance checks without claiming they passed. Focused validation fixes may now be
committed and pushed to `origin/feat/hardening-pass` for CI. No merge, tag, release,
unrelated provider writes, live user installation changes, or invented human/platform
results. The former prohibition on M2 work is superseded by the new authorization;
M3/M4 remain out of scope. Earlier commit/push permission was specific to M1 validation. Public test interfaces
were confirmed earlier in `plan.md`. The earlier existing-checks-only choice applied
to the completed initial composer increment, not this M1 acceptance-closure work.

## Current priority: aesthetics and UX

The operator elevated the aesthetics/UX pass above remaining roadmap work after
reviewing the live workspace screenshot. The working tree now includes a
history-first shell, responsive pane/metadata priorities, quieter palettes and
navigation, grouped large-commit inspection, and new visual/interaction coverage.
All **242 targeted UI regressions pass** in disjoint bounded partitions; root lint,
formatting and whitespace checks pass. Current light/dark/narrow/review/composer
renders are linked from README. See `aesthetics-design.md` and
`aesthetics-validation-manifest.json`. This is a UI iteration, not M2 acceptance or
publication authority; no commit or push is implied.

The follow-up Settings/Activity sizing request is also implemented: viewport-sized
frames, responsive content layouts, pinned footers, and repaired Select geometry.
**49 targeted regressions pass**, with reviewed wide/narrow/light/dark captures.
See `dialog-layout-validation.md`. These remain headless UI checks, not human or
platform acceptance. HEAD advanced externally during the follow-up; this session
did not issue a commit or push.

## Current M2 state

- Investigation, bounded captured history/search, comparison/file investigation,
  settings/keymaps, themes and read-only split inspection are implemented in the
  uncommitted working tree. Usage and limitations are in `docs/investigation.md`.
- Full bounded regression: **880 passed**; two subsequent public search/Focus
  tests also pass. Ruff, formatting, whitespace and CLI help pass. Selected-module
  Pyright has 44 inherited errors and no new diagnostic signatures versus baseline.
- Reference workload verifies 100k commits, 20k files, 1k refs and 200 changed files.
  Search completes all 100k; App retains 300 commit rows. Twenty explicit warm
  refreshes have median **1.403 s**, p95 **1.566 s** and maximum **1.633 s**—all
  above the ≤1 s warm-workspace target. M2 is **not accepted**.
- See `m2-execution.md` for scoped evidence, recorded timing-cap deviations and
  remaining performance, V08/V11 and platform/human checks. No M2 publication or
  commit/push authority is implied by the historical M1 permissions below.

## Accepted M1 baseline

- Validated implementation is `19086a3`. Application source is unchanged from
  `44cdd16`; subsequent fixes stabilize refresh/identity fixtures, pin the minimum
  Git compiler dialect, order tmux smoke teardown, and handle spaced constraint
  paths with real uv. The full-M1 acceptance follow-up changes documentation only;
  detailed local validation artifacts retain their original candidate identities.
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
  eight CLI/distribution cases pass together. After stabilizing a timer-sensitive
  refresh fixture, all 746 cases passed exactly once locally on `80f5e8f` in four
  partitions. Hosted Linux then exposed two missing-identity fixture failures;
  both reproduce locally with strict Git identity and pass after the `0a2805f`
  fixture repair. The historical failed runs remain preserved. Exact-candidate
  `19086a3` hosted CI now passes all seven jobs: Linux full suite **746 passed**,
  current/minimum tools, Linux/macOS tmux, macOS non-installer tests/build and
  benchmark tooling smoke. The macOS job is ARM64, not Intel.
- Dedicated composer tests pass on current Git and Git 2.36.0. Real 80×24 native
  commit journeys pass on both. Full current and minimum-tmux startup, popup,
  hotkey and pane journeys now pass in hosted CI.
- Real install/update/rollback/uninstall passes on macOS ARM64 and Ubuntu 24.04
  ARM64 in the existing VM, plus Linux AMD64 under existing emulation. The Docker
  bind-mount blocker was bypassed using the copy API, with no daemon/VM changes.
  The runs verify private uv/Python, exact revisions, spaced paths and preserved
  synthetic state/configuration. Successful containers/sandboxes were removed.
- The operator authorized clearing the remaining 21-file formatting debt. All 21
  files now match Ruff with identical before/after ASTs. Root lint, whole-tree
  formatting and whitespace checks pass. That formatting increment changed no
  behavior; later validation fixes intentionally changed fixture setup without
  weakening assertions. The installer retains activity after signal-style exits,
  without automatic recovery claims. Local packaging and hosted builds pass.

## Evidence and navigation

- `m2-execution.md`: current bounded pieces, results, failures and remaining gates.
- `m2-investigation-plan.md`: approved captured-scope interfaces and semantics.
- `m2-final-regression-manifest.json`: disjoint 880-case regression partitions.
- `m2-reference-repeated.json`: raw reference run and 20 explicit warm samples.
- `m1-completion.md`: current checklist, increment timings, commands/results and gaps.
- `m1-composer-validation.md`: exact current/minimum Git composer results.
- `m1-regression-*.txt`: complete bounded regression partitions and retained failures.
- `m1-terminal-*.txt`: isolated real-terminal results, including superseded failures.
- `m1-static-summary.txt`, `m1-build-local.txt`: earlier static/build outcomes.
- `m1-formatting-followup.json`, `m1-formatting-tests.txt`: current 21-file formatting
  proof and affected-test results.
- `m1-installer-repair.md`: crash/retry cause, isolated verification and limits.
- `m1-release-validation-44cdd16/README.md`: original complete-collection failure
  and candidate packaging/static checks.
- `m1-release-validation-80f5e8f/README.md`: all 746 local cases passing; hosted logs.
- `m1-linux-validation-followup.md`: Linux fixture and minimum-Git build repairs.
- `m1-full-release-19086a3/README.md`: current CI/lifecycle evidence and the exact
  remaining acceptance decisions. This supersedes stale pending-CI/install wording.
- `m1-linux-live-installer-19086a3/`: Docker-copy harness, ARM64/AMD64 results and
  preserved earlier bind-mount/probe failures.
- `docs/validation/m1-performance.md`: full reference workload and startup acceptance.
- `docs/validation/m1-compatibility.md`: local versus pending environment matrix.
- `docs/validation/m1-usability.md`: real-user pilot plan, not fabricated results.

Historical execution detail remains in `evidence.md`, `commit-restoration.md`,
`composer-progress.md`, `fetch-root-evidence.md`, `refresh-race.md`, and the original
planning artifacts. Those reports describe their own candidates, not this final
local working tree.

## Outcome / remaining acceptance

**M1 is complete and accepted for a full release.** No known failure remains in
the completed CI and macOS/Linux lifecycle checks. Startup timing is accepted,
formatting debt is cleared, and the installer crash/retry regression passes.
The operator explicitly deferred native Intel macOS, human-visible/WSL/SSH/clipboard,
remaining cold-cache/slow-provider/resource measurements and the five-person pilot.
These remain unverified, not passed. See `docs/validation/m1-release-acceptance.md`.
This roadmap task remains open for the newly authorized M2 work. M2 is in progress;
no M2 completion, merge, tag or publication is claimed.
