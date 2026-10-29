## Judgment

The current sequence is a V01 blocker: `patches.py` finishes validation and only then asks Git to acquire `index.lock` (`src/rhizome/patches.py:187-194`). A lock-respecting writer can therefore win and have its update overwritten. Existing tests cover stale state before `apply` and a pre-existing lock, not this window (`tests/test_patches.py:215-253`).

The proposed transaction is the smallest sound correction for ordinary Git concurrency. Put it behind a private Repository-level helper, as the approved analysis anticipated (`.ledger/.../patches.md:127-132`). Keep the public `PatchService` and `Repository` interfaces unchanged.

## Recommended transaction

1. Resolve the worktree-specific index using:
   `git rev-parse --path-format=absolute --git-path index`.
   Do not assume `<root>/.git/index`; linked worktrees have their own administrative index. Also replace the current broad `rstrip("\n")` behavior in `_git_path` with one-record-terminator removal (`src/rhizome/git.py:777-780`).

2. Open the index parent directory and create the real `index.lock` with `O_CREAT|O_EXCL`, preferably fd-relative with `O_CLOEXEC` and `O_NOFOLLOW` where available. Any existing object at that name means refusal; never inspect and remove a supposedly stale lock.

3. Under that continuously held lock:
   - Copy the current raw index into a securely app-owned alternate index.
   - If no index exists, leave the alternate index pathname absent; a zero-byte file is not a valid empty Git index.
   - Run the existing `update-index -z --index-info` against that alternate index.

4. Add only a narrowly typed private `index_file=` override to `Repository._git`/`_run_process`. `_git_environment` must first remove inherited repository/config variables, as it does now (`src/rhizome/git.py:49-59,153-159`), and then set `GIT_INDEX_FILE` in that one subprocess environment. Do not accept a general environment dictionary and never mutate `os.environ`.

5. Fsync the completed candidate and verify it is a regular file. Atomically rename it over the placeholder `index.lock`; the standard lock pathname remains continuously present. This preserves Git-produced file mode better than manually recreating the final index contents.

6. Perform the full `_open`/`_require_current` validation against the still-old real index while `index.lock` exists. Then immediately rename `index.lock` to `index`, which is the atomic visibility point, and fsync the index directory.

This order minimizes the unavoidable worktree/HEAD race: all expensive candidate construction and fsync happen before the final validation, leaving only the final rename afterward.

On failure before the final rename, the real index remains unchanged. Clean only lock/temp paths whose ownership and inode identity are known. If directory fsync fails after the final rename, report an explicit “index replaced, durability uncertain; refresh” outcome—rollback is no longer safe.

## Index representations

- **Split index:** The main index is not self-contained; it can reference `$GIT_DIR/sharedindex.*`. Let the repository’s own Git read and rewrite the alternate copy rather than parsing or flattening it. Query the candidate’s shared-index path, verify/fsync any referenced sidecar and its directory before publication, and never delete shared-index files during cleanup. If this does not work on the supported minimum Git, fail closed for split indexes rather than forcing `--no-split-index`.
- **Sparse index:** Let Git expand or retain sparse-directory entries. Logical index correctness and preserved skip-worktree state are mandatory; preserving the compact representation is desirable but not a data-safety requirement. Expansion is a defensible performance limitation, not silent corruption. Do not force `--no-sparse-index` without a separate product decision.
- **Unborn/no index:** Supported if the alternate path starts nonexistent and Git creates a valid index. Deleting the last entry may leave a valid zero-entry index, which is fine.
- **Linked worktrees:** Lock and replace only the index reported by `--git-path index`. Other linked-worktree indexes must remain untouched; shared refs and objects remain outside this transaction.

The current “full-index fingerprint” is actually a hash of `git ls-files --stage -z` (`src/rhizome/patches.py:334-338`), not of raw index bytes or every extension/flag. That is adequate for the approved line-mapping contract because the transaction seeds from and preserves the actual locked index, but it should not be described as a byte-for-byte index fingerprint.

## Atomicity boundaries

The transaction can guarantee:

- No lost update from lock-respecting Git index writers.
- Readers see either the complete old index or complete new index.
- A writer that commits before Rhizome acquires the lock is caught by under-lock revalidation.
- A writer that starts after Rhizome owns the lock fails normally and may retry afterward.

It cannot atomically cover:

- Worktree edits, ref/HEAD updates, Git configuration changes, or object pruning; those have separate or no lock protocols.
- A worktree/HEAD change after its final read but before the final rename.
- Processes that directly overwrite the index while ignoring `index.lock`.
- Hostile same-UID filesystem races. The existing worktree parent-`lstat` followed by path open also has a symlink TOCTOU boundary (`src/rhizome/patches.py:340-370`).
- SIGKILL or power failure before cleanup, which can leave an owned stale lock/temp. Future runs must not automatically break it.
- Filesystems that do not provide Git’s assumed `O_EXCL`/atomic-rename semantics.

Concurrent worktree/ref operations that overlap this transaction can defensibly be ordered after its index commit. If the requirement instead means “reject every change physically occurring before the rename,” that is a hard blocker: Git offers no transaction spanning index, refs, and arbitrary worktree files.

## Deterministic real-Git regression matrix

| Case | Controlled event | Required oracle |
|---|---|---|
| Writer wins before lock | Run real `git add other` immediately before private lock acquisition | Apply rejects; writer’s staged blob remains exact; target unchanged |
| Rhizome wins lock | After real `index.lock` exists, run real `git add other` | External Git fails on lock; selected result is exact; retry afterward preserves it |
| Final stale check | After alternate `update-index`, modify worktree or update HEAD to a prebuilt commit | Apply rejects; original index exact; owned lock/temp cleaned |
| Unborn/no index | Begin with no index and partially stage a new file | Valid index created; exact selected blob; worktree unchanged |
| Linked worktree | Apply from a linked checkout | Linked index changes exactly; primary worktree index remains byte/logically unchanged |
| Split index | Enable split index with unrelated staged entries | Exact blobs/flags preserved; `write-tree`, `status`, and shared-index resolution succeed |
| Sparse index | Cone sparse checkout with an outside sparse directory | Selected blob exact; outside logical entries and skip-worktree state preserved; status/write-tree succeed |
| Ownership/failure | Pre-existing lock, candidate Git failure, and pre-publish rename/fsync failure | No foreign lock removal; real index/worktree unchanged; only owned temporary artifacts removed |

Use private monkeypatch/barrier points around the transaction helper; no production test hook or new public interface is warranted.

Until the first two race cases and the split/sparse cells pass, ordinary-index support can proceed only if those unproven representations are truthfully gated.