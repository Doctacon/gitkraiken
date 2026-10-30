# Pull / Fetch split button — authorized implementation

User supplied a GitKraken-style split button with Fetch All, Pull (fast-forward if
possible), Pull (fast-forward only), Pull (rebase). After being told existing Pull
and conflict finishing are gated and full implementation is a larger backend task,
the user explicitly selected **Implement all four modes**. Narrow pull-owned
merge/rebase/Continue/Abort is authorized; generic M3/M4 actions remain gated.
No commits/pushes, live repository mutations, or external network validation.
Work in independently bounded <=15-minute increments; record partial completion
rather than claiming unsupported behavior. Use disposable local bare remotes.

The user also explicitly approved new tests at all four proposed interfaces:
preference validation/storage, real-Git pull coordination, native integration
processes/hooks (including practical terminal checks), and actual Textual workflows.
The temporary test-writing pause is lifted.

## Product behavior

- Main segment executes the saved mode; chevron opens anchored radio-style menu.
- Choosing a mode only saves preference; never fetches or integrates automatically.
- `pull_fetch_default`: `fetch-all`, `pull-ff` (initial screenshot default),
  `pull-ff-only`, `pull-rebase`. Respect global/workspace/excluded-session semantics.
- `Action.pull_mode`: `ff`, `ff-only`, `rebase`; empty means legacy ff-only.
- Current checked-out born branch and its exact configured remote upstream only.
- Fetch using existing validated fetch machinery, then revalidate local invariants
  and capture exact fetched upstream OID. Do not invoke native `git pull`.
- Equal/local-ahead => fetch-only success. Proven behind => fast-forward in all modes.
- Diverged ff-only => explicit refusal (fetch caches may have changed, local work not).
- Diverged ff => exact-target merge review, history checkpoint, merge.
- Diverged rebase => eligible linear unpublished/unprotected range review, checkpoint,
  exact rebase. No silent dropping, flattening, auto-stash, or updating other refs.
- Conflict => paused operation with owned recovery session; Continue/Abort supported.
  External operations remain gated. Restart discovers ownership, never replays work.
- Narrow/high-contrast/plain UI, keyboard and mouse, offline/busy/modal safety remain.

## Shared interfaces (backend owner implements in operations.py / pull.py)

`OperationCoordinator(..., pull_journal: RecoveryJournal | None = None)` uses a
SEPARATE pull journal argument: passing it must NOT enable generic discard or any
other previously gated operation. Existing `journal` semantics remain unchanged.

Public coordinator methods:

```python
prepare_pull(action: Action) -> PullPlan
submit_pull(plan: PullPlan, *, confirmed: bool = False) -> str
cancel_pull(plan: PullPlan) -> None
managed_pull() -> ManagedPull | None
```

`prepare_pull` accepts `pull`, `continue`, `abort`. Only `pull` performs fetch;
finish requests review exact current owned operation/index/worktree state. Plans
are frozen, coordinator-owned, bounded and single-use, including cancellation.

`PullPlan` exposes:
- `action: Action`, `repository: str`, `fingerprint: str`
- `available: bool`, `reason: str`, `requires_confirmation: bool`
- `consequences: tuple[str, ...]`
- `kind: str`: noop, fast-forward, merge, rebase, continue, abort
- `branch: str`, `original_head: str`, `target_oid: str`, `upstream: str`
- `remote: str`, `source_ref: str`, `incoming: int`, `outgoing: int`

`ManagedPull` exposes `kind` (merge/rebase), `branch`, `original_head`,
`target_oid`, `checkpoint_id`. Invalid/changed ownership must fail closed, not
adopt an external operation. `prepare_pull`/`submit_pull` report activity through
existing coordinator activity. A paused conflict returns a descriptive result
and retains active checkpoint/metadata; uncertain outcomes raise GitOutcomeUncertain
and remain protected. Abort requires explicit confirmation, including discarded
resolution edits, and refuses untracked/ignored obstruction loss.

`PullPlan` and `ManagedPull` may live in new `pull.py` and be re-exported from
operations.py. UI needs only these values and methods, never Git internals.

## Native local mutation primitive (separate owner)

`Repository._run_integration(args: list[str], *, operation: str,
on_spawn: Callable[[int], None] | None = None) -> CompletedProcess[bytes]`

- Args contain exact already-validated local Git integration command; return even
  on nonzero Git exit so coordinator can classify conflict vs failure/uncertainty.
- No deadline, cancellation, automatic retry, or killing unfinished Git/hooks.
- Reuse the established foreground terminal lease pattern (`_commit_terminal`),
  regular diagnostic spools, process_group=0, bounded output and surviving-child
  detection. Preserve hooks/signing, working-directory/environment isolation.
- on_spawn records PID/PGID durably in session metadata before waiting; failures
  after spawn are uncertain. Native non-headless caller suspends Textual first.
- Must not alter ordinary commit transaction behavior.
- Direct `Repository.execute(Action('pull'))` becomes an explicit refusal requiring
  coordinator review; preserve old low-level test via migration, not bypass.

## Safety and validation checklist

- Review identity, exact full local ref/HEAD, upstream remote/source/tracking mapping,
  config, index, worktree, operation markers, shallow boundary, pre-fetch remote tips.
- Revalidate after fetch and checkpoint and immediately before integration; ignore
  movement of fetch caches only during the expected transport step. Never chase a
  target that moves after review; no branch/config/ref retargeting.
- Reject detached/unborn/no upstream/local-dot remote/ambiguous or excluded mapping,
  dirty index/worktree, conflicts or existing operation. Do not drop user files.
- Disable nested submodule recursion/network and auto-stash. Preserve ignored
  obstructions; do not reject harmless ignored build trees just for existing.
- Native configuration must not widen selected merge/rebase policy, cause squash,
  edit, auto-stash, other-ref rewrites, or silent cherry-pick dropping.
- Shallow ambiguity fails closed. Rebase requires one merge base, bounded outgoing
  range, no merge commits, no outgoing commits reachable from known remote tips
  before OR after fetch; configured/default protected branches cannot be rewritten.
- Durable history checkpoint required for divergent merge/rebase; failure blocks
  integration without claiming the preceding fetch was rolled back.
- Only pull-owned operation metadata enables Continue/Abort. On repeated conflicts
  retain session. Verify completion/abort postconditions, never auto-retry uncertain
  mutations. History checkpoint remains protected when outcome is uncertain.
- Keep generic merge/rebase/reset/stash/external Continue/Abort gates unchanged.
- Tests use real local repositories for FF/refusal/merge/rebase/conflict/finish,
  restart ownership, stale plans, config drift, recovery failure, hook failures,
  unrelated refs/files preservation and no automatic network action on menu choice.

## Ownership

- Backend builder: operations.py, recovery.py, new pull.py, corresponding new
  tests/test_pull.py and recovery tests. No app/settings/models/git.py edits.
- Native builder: git.py and native primitive tests (new test_pull_process.py),
  existing test_git.py direct-pull migration as needed. No coordinator/UI edits.
- Designer: app.py, app.tcss, workspace_controls.py, new pull_controls.py, new
  tests/test_pull_controls.py; update affected test_app.py tests. No backend edits.
- Main: models.py, settings.py, persistence.py, settings tests, docs, capture
  tooling, ledger, integration review and final regression.

## Bounded integration checkpoints (historical)

These describe intermediate candidates, including superseded pending statements.
Final local evidence and remaining limits are in `pull-fetch-validation.md`.

- Preference validation/persistence: 52 combined preference/storage cases passed.
- Initial native primitive: 57 process/Git cases, 37 fetch-safety cases and seven
  commit/PTY regressions passed. No native mutation deadline or implicit kill.
- Initial backend: 28 pull/safety/recovery cases and 72 operations/fetch cases
  passed, but independent review exposed ownership/recovery gaps; those counts
  were insufficient to claim readiness.
- Hardened backend: durable common-Git-directory writer lease, bound operation
  manifests, pre-Continue recovery, staged/worktree preimages, revalidated remote
  protection and unrelated-ref postconditions. 34 pull/safety/recovery cases and
  72 operations/fetch cases passed. Eleven safety cases also passed on the actual
  Git 2.36.0 binary. Full minimum-version conflict/finish validation remains pending.
- Real app tests exposed automatic FF/Continue being incorrectly treated as
  cancellation and idle control updates enabling external Abort. Root fixed both;
  eight real local-Git app journeys plus the initial menu test passed (63.35 s).
- Actual 80×24 capture exposed the popup's parent clipping its last two options
  despite the OptionList's own region appearing tall enough. Root corrected outer
  frame sizing and added parent-bound geometry plus real last-row click coverage.
  Updated `/tmp/rhizome-pull-menu-80-final.png` visibly shows all four modes.
- The final integration pass completed live descendant/lease restart tests, actual
  UI staging, minimum-version backend checks and broader targeted regressions.
  It added explicit refusal/retry for untracked finish states, hidden-index finish
  refusal, protection reload during a paused rebase, actual saved Fetch All and
  FF-only clicks, and native PTY coverage for the new integration runner.
- The implementation is locally verified, not a human-usability or release
  acceptance. Generic/external M3/M4 actions remain gated. See the final report
  for exact counts, six-defect regressions, source identity and validation limits.
