# Execution plan

## Authority and constraints

The operator requested: “Execute everything with your best recommendations.” They then explicitly approved the proposed public test interfaces and creation of this execution ledger. Product behavior is governed by the approved design and validation documents, not by a planner's implementation suggestions.

- Implement M1 → M2 → M3 → M4 in dependency order; do not relabel a partial milestone complete.
- Use real disposable Git repositories/local bare remotes for mutation correctness. No tests on the user's checkout or credentials.
- New tests use confirmed public interfaces and observable behavior, with sequential red → green slices rather than bulk imagined tests.
- Every enabled risky action must have proven protection. Gating unproven actions is allowed; safety exceptions are not.
- Keep production Git subprocess ownership coherent and integration of `app.py`/`git.py` centralized. Do not build a speculative plugin framework.
- Private provider access, publishing, commits, releases, and real-user recruiting require separately appropriate authorization/resources. Do not fabricate unavailable evidence.

## Confirmed test interfaces

1. `Repository` public queries/operations and `PatchService.open/apply`: real Git/index/worktree effects, stale selection and unsupported representations.
2. `WorkspaceStateStore.load/save/clear`: private durable settings, drafts/context, identity and corruption behavior.
3. `RecoveryJournal.prepare/complete/list/pin/purge/recover_aside`: admission, durability, retention, recovery-aside and failures.
4. `OperationCoordinator.plan/submit/activity`: applicability, risk, exact targets, scheduling and uncertain outcomes.
5. `InvestigationService` public query interface: history scopes, pages, complete search, comparison and file investigation.
6. `HistoryPlanner.plan` and `ConflictSession` public resolution/operation methods: eligibility, actual commits, explicit stage/continue and recovery.
7. `ReviewProvider.capture` and `ReviewWorkspace.open/fetch_missing`: exact provider identities and isolated snapshots.
8. Pure `render_history` and `RhizomeApp` through Textual Pilot: visible graph geometry and real keyboard/mouse flow.
9. Public CLI/bootstrap subprocesses and isolated tmux smoke: process/configuration effects and diagnostics.

Exact internal implementations remain delegated within those interfaces. A genuinely new test interface needs fresh confirmation, not a backdoor test of private collaborators.

## Ordered work

### Foundation and M1

1. Baseline tests and environment record.
2. Remove mutation semantics from browsing double-clicks; inventory/gate unsafe entry points.
3. Canonical patch documents and exact hunk/line stage/unstage through real-Git proof.
4. Private settings/drafts/context and recovery admission/content/retention interfaces.
5. Operation planning/protection and visible activity; avoid blocking local reads behind hosted requests.
6. Integrate keyboard/mouse diff selection, stable review, assisted composition, 80×24 task focus, and durable drafts.
7. Re-enable guarded actions only as protection is proven; preserve explicit external hand-offs for the rest.
8. Managed pinned bootstrap/explicit updates, diagnostics, compatibility automation and benchmark fixtures.
9. Run candidate correctness, local platform/terminal checks, and reference measurements. Record external/human gaps without calling M1 releasable.

### M2

Captured scope, topology/chronology, explicit focus, bounded full-history reachability, complete-scope search, intent-aware comparisons, file history/blame, split rendering, keymap/settings UI, selectable/adaptive accessible themes.

### M3

Eligible linear history plans, known-publication/default-ref protection, offline uncertainty acknowledgment, recover-aside, guided conflicts with explicit review/stage/continue, divergence, clean-only Git-managed worktree lifecycle.

### M4

Immutable GitHub.com/Azure source/target identity and OIDs, same-repository isolated review, explicit missing-data fetch, stable review/update notices, honest capability/check states, browser-only fork/write hand-offs.

## Ownership discipline

- Main integrator: `app.py`, `git.py`, `models.py`, `context_actions.py`, broad existing integration tests, final docs and task status.
- Independent workers may own new patch, persistence/recovery, operation, investigation, integration, provider, UI, or distribution modules and their own focused tests.
- Never let two workers edit the same file concurrently. Freeze a compact interface contract before wiring independent modules.
- Preserve intentional UI structure; mechanical integration must not erase it.

## Validation and stop conditions

Each implementation slice records red/green commands and results. Validate actual index/files/refs rather than a duplicate of the implementation's patch calculation. If an unsafe or unsupported case cannot meet its contract, refuse it honestly and record the blocker rather than mutate anyway.

CPU/memory budgets remain to be proposed from evidence. Human five-user acceptance, other OS/architecture/minimum-version checks, live provider fidelity and screen-reader claims require actual evidence; local headless tests alone cannot satisfy them.
