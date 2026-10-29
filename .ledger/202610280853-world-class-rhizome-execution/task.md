Status: open
Created: 2026-10-28
Updated: 2026-10-28

# Execute the approved world-class Rhizome roadmap

## Intent

Execute the approved M1–M4 roadmap in `docs/design/world-class-rhizome.md` and its validation plan. The operator explicitly authorized implementation and local validation after approving the two design documents, then confirmed the public test interfaces and creation of this execution ledger. Preserve the approved product contracts; use targeted refactoring, real-Git correctness evidence, and fail-closed safety.

## Current State

- Starting baseline: `70005ce6dd23510d6f16af73ff5247fba32b81dc` on `main`.
- The two approved design documents are existing untracked work and must be preserved.
- Confirmed host: macOS/Darwin ARM64; Git 2.50.1 (Apple Git-155); tmux 3.5a; Python 3.13.0.
- Existing baseline: `uv run --locked pytest` (background task `task-1`) passed all 456 tests, exit 0 (~565 seconds).
- Non-mutating sidebar/graph browsing has focused green regression evidence. Initial canonical patch, durable state/diagnostics, recovery, and precision-prototype modules are present; integration and broader validation remain pending.
- Operation coordination is now wired for routine mutations and canonical range/hunk staging after focused safety proof. The workspace has no recovery journal attached yet, so destructive workflows stay unavailable rather than bypassing protection.
- Selected-patch index publication has real-lock, competing-writer, split/sparse, linked-worktree, and failure-path evidence. Ordinary commit remains blocked in coordinator/UI. Raw-index/config binding, read-only inspection, bounded output, recovery observations, live-descendant retention and artifact-ownership checks now have focused proofs. Native commits use a dedicated inherited process group and require absence before publication; unresolved liveness/spawn outcomes retain artifacts, never kill/retry. Interactive signing/TTY compatibility, composition, remaining publication faults and coordinator/UI success proof remain open. Earlier 80×24 commit flows predate the gate; no bypass or success-test migration is allowed. See `commit-root-followup.md`.
- Commit and intent-keyed PR drafts survive reopening; second-target PR isolation is covered. Offline/remote cadence and graph-style preferences are integrated. Broader state failure/lifecycle and settings work remain.
- Background transport no longer holds the local inspection queue; real Git stalled-transport, mutation/read-overlap and hidden/focus regressions pass. Mutations remain serialized. Compact activity is integrated and its 80×24 stage/outcome/Close test passes; broader queued/running UI evidence remains.
- Second full discovery finished with 1 failure and 586 passes (`task-9`, `integration-test-run-2.txt`). The unchanged stale hosted-result regression exposed a real bug, now fixed with independent ABA coverage. Refresh/sidebar/state batch passed 36 tests in 103.95 s (`task-13`, BEFORE the commit gate). No changed full suite has passed; current core commit success tests must remain red until protection is proven. See `evidence.md` and `refresh-race.md`.
- Installer recovery hardening had 61 passing installer/CLI/persistence tests (`task-23`), but a new intermediate-launcher regression is red: a second installer succeeds while a surviving child remains active. Root reproduced 1 failure in 3.38 s. No production fence fixes this yet; V05/live installation remain blocked. See `distribution-guard-quiescence-result.md`. Boundary doubles are not installation or release acceptance.
- Fetch scope repair is integrated and manual/automatic fetch are restored after public coordinator/UI proof. It rejects unsafe destinations/pruning and existing inbound user-ref aliases whose tips would move through a cache. Real tests exposed three old tips becoming unreachable through branch/tag/notes aliases; fixed. Current and actual Git 2.36 fetch suites: 37 passed each; manual/Pilot + automatic/refresh/sidebar/activity batch: 29 passed in 97.37 s (`task-25`). Commit remains gated. See `fetch-root-evidence.md`.
- Forced split-index shared-base rebuild exposed native candidate GC deleting storage still referenced by the real index. Per-call expiry deferral fixed commit and selected-patch cases. The 44-case minimum-Git rerun passed; later 46-case runs passed on both versions. Root expanded discovery (`task-22`): current Git 126 passed; Git 2.36 123 passed/3 failures. Two compatibility defects were fixed; the third was a missing HTTPS helper in the disposable build, corrected without installation. Subsequent minimum-Git engine/fetch slice passed 73 tests. Latest combined engine/patch/native/fetch/read-only/plan validation (`task-27`) passed 151 tests on current Git in 120.02 s and 151 on actual Git 2.36 in 56.47 s, including linked+split and artifact ownership. This is not full-suite or coordinator/UI commit acceptance.
- Commit-template facts and `composer-implementation-plan.md` are collected from pinned Git v2.36 sources. The planner proposes private template/encoding helpers and editor-mode native stdin delivery; this is not oracle or acceptance evidence. Composer template/cleanup behavior is not implemented. Draft failure/corruption, linked-worktree/recreated identity and hook-retention tests exist; hook/commit UI successes predate the gate.
- Public test interfaces explicitly confirmed before adding/editing tests; see `plan.md`.
- No commits, publication, real private-provider mutations, human-pilot claims, or untested-platform claims are authorized or implied.

## Outcome

In progress. Implementation and validation results will be recorded in `evidence.md`; blocked external/human checks remain explicit. No milestone is currently claimed complete.
