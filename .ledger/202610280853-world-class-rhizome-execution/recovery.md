# Recovery journal implementation

## Integration API

Implemented in `src/rhizome/recovery.py`:

- `RecoveryJournal(root: Path, *, max_bytes: int = 1024**3, max_age_days: int = 30)`
- `prepare(repo: Repository, *, operation: str, paths: tuple[str, ...] = (), history: bool = False) -> Checkpoint`
- `complete(checkpoint_id: str, outcome: str = "succeeded") -> None`
- `list(repository: str | None = None) -> tuple[Checkpoint, ...]`
- `pin(id: str, pinned: bool = True) -> None`
- `purge(id: str) -> None`
- `recover_aside(repo: Repository, id: str) -> RecoveryResult`

`Checkpoint` and `RecoveryResult` are frozen dataclasses. `Checkpoint` exposes `id`, `repository`, `operation`, `head`, timezone-aware `created_at`, exact retained regular-file `size_bytes`, `active`, and `pinned`. `RecoveryResult` exposes `branch`, `directory`, and a human-readable `summary`. Fail-closed errors use `RecoveryError`.

Known terminal outcomes are `succeeded`, `failed`, and `cancelled`; another outcome remains active and protected. `purge` is the explicit removal control and can remove active/pinned content. No method replays an operation.

## Supported protection scope

- Captures only explicitly requested paths which are tracked in index stage 0 or captured `HEAD`.
- Captures separate index and worktree preimages, including present/absent state, Git mode (`100644`/`100755`), size, index OID, and SHA-256. `files/preimages.json` is the review manifest; copies live separately under `files/index/` and `files/worktree/`.
- Supports ordinary regular tracked files, staged additions/deletions/renames, mixed staged/unstaged bytes, and executable identity. It rejects conflicts, symlinks (including symlink ancestors), submodules, special files, untracked-only requested paths, unsafe/noncanonical paths, and changed-during-capture identities.
- `history=True` creates and verifies a self-contained private Git bundle for all refs plus captured `HEAD`. This conservatively protects more refs than a current-branch-only implementation. It preflights with twice Git's reported object storage plus 64 KiB and refuses before bundling when that estimate does not fit the journal budget/free space. Exact bundle bytes are included in admission/accounting after creation.
- Recovery validates metadata/payload fingerprints and exact byte accounting. It imports bundle objects with `git bundle unbundle`, creates a unique `rhizome-recovery/<checkpoint-prefix>` branch at captured `HEAD`, and never restores original refs or checks out the branch. File/index recovery exposes the two retained review directories. HEAD, index, worktree, and unrelated untracked files are not changed.
- Storage and lock files are owner-only; checkpoint directories are `0700`, retained nonexecutable files are `0600`, and executable review copies are `0700`. Publication is same-directory rename after file/directory fsync. Journal public operations use an advisory exclusive lock.
- Automatic cleanup removes expired or oldest over-budget completed/unpinned checkpoints. Active and pinned checkpoints are never automatic eviction candidates. Corrupt/unknown checkpoint state causes refusal rather than cleanup. Unpublished prepare staging is discarded on restart and never triggers an operation.

## Behavioral evidence

Real disposable repositories and filesystem observations are in `tests/test_recovery.py`:

- Literal mixed index/worktree content and executable mode; unrelated untracked/newer work preserved.
- Staged deletion and rename present/absent identities.
- Source branch deletion, reflog expiry, immediate pruning, then bundle-backed recovery of the literal commit/content to a new branch while current checkout remains unchanged.
- Exact retained file-byte accounting, restart/list/filter, pin/purge, active/unknown outcomes, age expiry, and budget admission/eligible eviction.
- Traversal and symlink-ancestor refusal.
- Payload corruption refusal without silent eviction.
- Injected atomic-publication failure with no published checkpoint and no repository mutation.
- Conservative history preflight refusal before bundle creation.

Red-to-green record:

1. `uv run --locked pytest -q tests/test_recovery.py::test_prepare_and_recover_aside_preserve_distinct_index_and_worktree_preimages` initially failed because final admission treated the in-progress staging directory as stale; admission was changed to evaluate/evict only already loaded checkpoints. The same command then passed.
2. Each subsequent behavior was run individually when added (history/pruning, absence/symlink, retention, corruption/publication/preflight, and rename), then the focused file was run together.

Final validation (2026-10-28 local macOS environment):

- `uv run --locked pytest -q tests/test_recovery.py` — **9 passed in 3.20s**.
- `uv run --locked python -m py_compile src/rhizome/recovery.py tests/test_recovery.py` — **passed** (no output).
- `uv run --locked ruff check src/rhizome/recovery.py tests/test_recovery.py` — **passed** (`All checks passed!`).

No full-suite, Linux/WSL2, process-kill, true disk-full, or multi-process stress validation was run in this bounded assignment.

## Residual limitations / required gates

- History recovery creates the branch at captured `HEAD`. Although the bundle retains all-ref objects, this API has no target-ref parameter; operations whose sole recovery target is a non-HEAD ref must remain gated or gain separately approved target identity semantics.
- Conservative history preflight can reject a bundle that might compress below the configured budget. This is intentional fail-closed behavior.
- An unborn repository has no commit history to bundle; `history=True` records no history payload until `HEAD` exists.
- Unsupported representations listed above must keep affected destructive actions gated rather than weakening protection.
- File/index review copies are the retained checkpoint payload itself, separated by state; there is no in-place apply or second editable export in this module.
- Advisory locking is implemented and ordinary restart is tested, but kill-at-every-instruction and concurrent-process stress remain unproven.
- Exact accounting is the sum of retained regular-file lengths (metadata, manifests, preimages, and bundle); filesystem allocation/directory-block overhead and the zero-byte advisory lock are not charged.
