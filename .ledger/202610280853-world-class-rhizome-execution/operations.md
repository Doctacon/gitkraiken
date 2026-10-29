# M1 operation coordinator

## Public API

Implemented in `src/rhizome/operations.py` and deliberately not wired to `app.py` in this assignment:

- `OperationCoordinator(repo: Repository, *, journal: RecoveryJournal | None = None, protected_branches: tuple[str, ...] = ())`
- `plan(action: Action) -> OperationPlan`
- `submit(plan: OperationPlan, *, confirmed: bool = False) -> str`
- `activity() -> tuple[OperationActivity, ...]`

`OperationPlan` is frozen and exposes `action`, repository path, single-use `fingerprint`, `available`, `reason`, `requires_confirmation`, and immutable `consequences`. Coordinator-issued plans are retained in a bounded 256-entry registry; altered, unknown, expired, concurrent duplicate, and replayed submissions fail closed.

`OperationActivity` is frozen and exposes `id`, `operation`, `state` (`queued`, `running`, `succeeded`, `failed`, or `uncertain`), redacted/bounded `message`, timezone-aware `started`, monotonic `elapsed`, and optional `checkpoint_id`. Activity retains the newest 20 records and returns an immutable tuple. There is intentionally no cancellation API: running Git mutations and fetches are not represented as generically cancellable. All methods remain synchronous and can be called with `asyncio.to_thread`.

## Safety and scheduling behavior

- Planning is read-only. It records exact root, worktree Git dir, and common Git-dir device/inode identities; HEAD/branch; operation-relevant index/worktree bytes; target OIDs/refs; worktree destination identity; local config; and remote/push destination state as applicable.
- Submission consumes plan authority before waiting, serializes mutations with an in-process lock keyed by the common Git repository inode (including linked worktrees), and revalidates after the queue wait. Exact identity replacement and stale reviewed state stop before mutation.
- Worktree review uses no-follow file descriptors and before/after inode/size/mtime/mode checks. Destination/config and target-ref changes are rejected.
- Confirmed discard with a real journal durably checkpoints each requested tracked path, then revalidates again before calling Git. Unsupported recovery representations or checkpoint admission failures stop the action. Known outcomes complete the checkpoint; unknown outcomes remain active under the journal contract.
- Push is explicitly non-force through `Repository`. Any Git failure after push execution starts is surfaced as `uncertain`, with an inspect-before-retry message; stale/pre-execution rejection is `failed`. Plans are never retried automatically.
- Conventional local `main`/`master`/`trunk`, detected remote default symbolic refs and their source refs, and configured protected refs are recognized. Ref rewrite/deletion stays unavailable; protected targets receive a specific explanation.

## Passed capability matrix (real disposable Git)

| Capability | Result and evidence |
| --- | --- |
| Stage | Enabled; literal selected content reaches the index; changed reviewed worktree bytes reject without index mutation. |
| Unstage | Enabled; exact selected index content is removed while worktree bytes remain; changed reviewed index rejects. |
| Commit | Enabled; reviewed staged bytes and message produce the asserted commit. Empty/message applicability is checked by planning. |
| Checkout | Enabled; asserted commit checkout works; moved selected ref rejects and leaves branch/HEAD unchanged. |
| Create branch | Enabled; exact target is used and checked out; destination ref nonexistence is revalidated. |
| Create tag | Enabled; exact target and destination nonexistence are revalidated. |
| Create worktree | Enabled; detached target and resulting checkout are asserted; destination creation/content after review rejects without registration/overwrite. |
| Set upstream | Enabled; exact full local ref, remote-tracking ref owner, reverse fetch mapping, refs, and config are checked; resulting upstream is asserted. |
| Fetch | Enabled; local bare remote tracking ref moves to the asserted remote commit. Config/destination state is reviewed. |
| Push | Enabled only through the existing explicit non-force refspec path; local bare destination receives the asserted OID. Changed URL rejects before execution as `failed`. A real timed-out Git/SSH process (test-shortened timeout) is `uncertain` and single-use/no-retry. This does not prove reconciliation after a server-side side effect. |
| Discard | Enabled only with explicit confirmation and an injected real journal. The worktree is restored from index only after checkpoint publication; `recover_aside` proves literal prior worktree and index copies. A tracked symlink unsupported by recovery stops before mutation. Without a journal it remains unavailable. |
| Common-repository serialization | A real blocking pre-commit hook in one worktree leaves a second worktree mutation visibly queued; the second mutation runs only after the first releases. |
| Plan authority and identity | Frozen plan fields, tamper rejection, replay rejection, moved repository/worktree inode rejection, and no mutation of either old/new path are asserted. |
| Protection | Conventional default branch, explicitly configured local protected branch, and a nonconventional remote default source ref are unavailable for deletion/rewrite; remote target remains present. |
| Activity | Frozen records expose queued/terminal state, elapsed/start/checkpoint data; 22 real mutations retain only the newest 20. |

All mutation/content assertions use temporary real repositories and local bare remotes. Tests inject temporary `RecoveryJournal` roots and never use user recovery state or credentials.

## Remaining gates and honest limits

Unavailable with explicit external-Git hand-off reasons:

- merge, rebase, cherry-pick, revert;
- soft/mixed/hard reset (hard reset specifically remains blocked for untracked obstruction risk);
- amend/edit-message;
- stash, stash apply/pop/drop;
- apply-patch, pull, continue, and abort;
- local/remote branch rename/deletion and tag deletion.

The current recovery API restores history only to a recovery branch at captured `HEAD`; it cannot prove exact restoration of an arbitrary deleted/renamed target ref. Therefore even “simple” ref rename/deletion remains gated despite a journal. No force push or remote deletion is enabled. No generic rollback, cancellation, or safe-undo claim is made.

Scheduling is bounded to in-process coordinator instances sharing this module. It does not claim cross-process exclusion from external Git, atomic rollback of hooks/config changes, server-side push reconciliation, or process-kill safety. Plan snapshots are conservative and can invalidate on some unrelated index/ref/config changes rather than risk applying stale intent.

## Validation evidence

Focused final validation on 2026-10-28 (local macOS environment):

- `uv run --locked pytest -q tests/test_operations.py tests/test_recovery.py` — **28 passed in 33.37s** (19 operation tests plus 9 recovery tests).
- `uv run --locked ruff check src/rhizome/operations.py tests/test_operations.py` — **passed**.
- `uv run --locked python -m py_compile src/rhizome/operations.py tests/test_operations.py` — **passed**.

The original three coordinator cases remained green: stale stage worktree rejection, discard gating without recovery, and moved checkout target rejection. The new public-shape test first failed on the missing constructor/options and then passed after implementation. Remaining cases were added in focused real-Git slices and run individually before the combined run.

No full suite, app wiring, private provider mutation, real remote publication, Linux/WSL2, process-kill, or human validation was performed here.

## Final outcome

Completed and intentionally left unwired to `app.py`. The coordinator's public plan/submit/activity policy is implemented and focused proof is green:

- `uv run --locked pytest -q tests/test_operations.py tests/test_recovery.py` — **28 passed in 33.37s**.
- After the final no-follow/content-fingerprint tightening, `uv run --locked pytest -q tests/test_operations.py` — **19 passed in 29.56s**.
- `uv run --locked ruff check src/rhizome/operations.py tests/test_operations.py` — **passed** (`All checks passed!`).
- `uv run --locked python -m py_compile src/rhizome/operations.py tests/test_operations.py` — **passed** (no output).

Enabled scope is exactly: stage, unstage, commit, checkout, create branch, create worktree, create tag, set upstream, fetch, non-force push, plus confirmed discard only with a real `RecoveryJournal`. The operation test file proves literal Git/index/worktree/ref effects, stale target/content/config/destination rejection, single-use authority, common-repository serialization, bounded immutable activity, protected refs, recoverable discard content, and fail-closed unsupported checkpoint content.

Unresolved limits remain deliberate gates. Inspection of `RecoveryJournal.recover_aside` confirms that its bundle contains all refs but the public recovery action creates only a new recovery branch at the checkpoint's captured `HEAD`; it does not expose an exact actionable restore for a deleted/renamed non-HEAD ref or a commit chain reachable only from that ref. Therefore branch/tag/remote-ref deletion and branch rename remain unavailable, including non-HEAD targets. Integration/reset/amend/stash/apply/continue/abort also remain gated pending M3 plans and coverage. Hard reset cannot threaten untracked data. Push timeout/failure is visible as uncertain and never retried, but server-side reconciliation is not implemented or claimed. Cross-process/external-Git races, atomic config rollback, app/UI integration, and non-local platform evidence remain incomplete.
