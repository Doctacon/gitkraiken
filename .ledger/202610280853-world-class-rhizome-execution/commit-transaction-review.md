## Judgment

The proposed Repository-private transaction is the smallest credible fix for the index race. It can guarantee exclusion of lock-respecting writers to the current worktree index, but it cannot make the ref update and final index publication jointly atomic.

The coordinator and UI gates should remain in place until the real-Git proofs below pass. No representation-specific compatibility bypass is appropriate.

## Key findings

1. **The current gap is real and remains outside coordinator serialization.**  
   `OperationCoordinator.submit()` revalidates under only its in-process lock at `src/rhizome/operations.py:300-305`; `Repository.execute(commit)` then checks and starts ordinary Git at `src/rhizome/git.py:2112-2122`. An external `git add` can win between them, exactly as demonstrated by `tests/test_operations.py:32-59`.

2. **`_replace_index_entries()` is the right mechanical precedent, not a directly reusable transaction.**  
   It already provides worktree-specific lock resolution, same-filesystem candidates, split-index sidecar checks, atomic publication, and post-publication durability uncertainty at `src/rhizome/git.py:474-576`. Commit needs a separate state machine because native Git may:
   - update HEAD before index publication;
   - mutate the alternate index through hooks even when the commit is rejected;
   - time out after the ref has advanced.

3. **Validation must occur inside the real `index.lock`.**  
   The coordinator’s existing full state check should remain as an early rejection, but the private commit transaction must invoke the reviewed-state validator again after acquiring the real worktree `index.lock`. The current index fingerprint is logical `ls-files --stage -z`, not raw index identity (`src/rhizome/operations.py:545-565`); copying the actual locked index preserves extensions and flags not represented by that fingerprint.

4. **Native `git commit` is essential.**  
   Do not replace it with `write-tree`/`commit-tree`. Running native commit with the alternate index preserves normal synchronous hook execution, configured signing, message cleanup, COMMIT_EDITMSG behavior, reflogs, and Git’s own compare-and-swap ref update. The existing per-call environment design is correct: inherited `GIT_INDEX_FILE` is sanitized, then the private absolute override is added only to that subprocess (`src/rhizome/git.py:228-244`).

5. **Template support should not be overstated.**  
   The current command is `git commit -m ...`, which bypasses `commit.template`; the app also strips the draft (`src/rhizome/app.py:2167-2172`). The alternate-index transaction is transparent to message/template behavior but does not itself add template semantics.

## Recommended sequence

Use a dedicated private Repository method taking the action and a validation callback; do not add transaction fields to `Action` or expose a general environment API.

1. Resolve the absolute, worktree-specific index path with `--git-path index`; pin its parent directory identity.
2. Create the real sibling `index.lock` using exclusive, no-follow, fd-relative creation. Never remove a pre-existing lock.
3. Copy the current raw index to an app-owned alternate index in the same administrative filesystem. For a reviewed ordinary commit, an index should exist; never treat a zero-byte file as an empty index.
4. While the real `index.lock` remains present:
   - invoke the coordinator’s full reviewed-state validator against the unchanged real index;
   - capture the full symbolic HEAD target and old OID, including an absent OID for unborn HEAD;
   - perform the staged-change check against the alternate index, not the unlocked real index.
5. Run native `git commit` with only this call receiving the absolute `GIT_INDEX_FILE`. Keep the real `index.lock` present throughout the process and all synchronous hooks.
6. Once the process and synchronous children are quiescent:
   - verify the alternate index is a regular valid index and no alternate lock remains;
   - fsync it;
   - query its split-index sidecar, verify/fsync that sidecar and its administrative directory;
   - inspect HEAD/ref outcome before deciding the user-visible result.
7. Publish the index independently of ref classification:
   - atomically replace the owned `index.lock` placeholder with the alternate index;
   - atomically rename `index.lock` to `index`;
   - fsync the real index directory.
8. Re-read HEAD/ref and classify the result. Never roll back a ref or blindly restore the old index after Git may have advanced HEAD.

Keeping publication independent of ref movement is important. A concurrent `update-ref` does not own the index; the stable alternate index remains the correct result of this index transaction even if the final commit outcome must be reported as uncertain.

## Hooks and signing

- Plain Git commands run by synchronous hooks inherit the alternate `GIT_INDEX_FILE`. Thus `git add` from `pre-commit`, `prepare-commit-msg`, `commit-msg`, or `post-commit` modifies the candidate rather than failing on the held real lock.
- Hook edits before commit creation may become part of the commit. Post-commit edits should remain staged in the published index. This is the necessary consequence of retaining native hook authority; the exclusion guarantee applies to competing ordinary writers, not authorized hook transformations.
- If a hook stages content and then rejects the commit, publish the stable candidate before returning the hook error. Ordinary Git preserves such hook-driven index edits.
- A normal pre-commit/commit-msg/signing rejection with unchanged HEAD is an ordinary failure after candidate publication.
- A nonzero `post-commit` hook normally cannot veto the commit; Git generally returns success. A hang or process failure in that phase is different because HEAD has already advanced.
- Configured signing remains active. A signing failure normally precedes ref movement, but signing can prompt or hang despite Git’s prompt environment, so timeout handling must remain conservative.
- Hooks that directly open `.git/index`, unset `GIT_INDEX_FILE`, daemonize, or mutate refs themselves are arbitrary filesystem actors. Exact compatibility with those behaviors is not achievable alongside alternate-index isolation.

## Outcome classification

| Observation | Result |
|---|---|
| Git exits 0, symbolic HEAD shape is unchanged, new commit is the expected direct child (or parentless initial commit), candidate publication and fsync succeed | Success |
| Git exits nonzero normally, HEAD/ref remains at the captured value, candidate publication succeeds | Ordinary Git failure; retain draft |
| HEAD/ref differs unexpectedly after any failure | `GitOutcomeUncertain` |
| Any timeout or signal | `GitOutcomeUncertain`, even if HEAD currently appears unchanged |
| HEAD advanced but candidate fsync/publication fails | `GitOutcomeUncertain`; never suggest blind retry |
| Final rename occurred but directory fsync failed | `GitOutcomeUncertain`, matching `_replace_index_entries()` durability semantics |
| Git exits 0 but HEAD is unchanged, has the wrong parent, or the symbolic target changed | `GitOutcomeUncertain` |

The current timeout path converts `TimeoutExpired` directly into ordinary `GitError` (`src/rhizome/git.py:261-264`) and only kills the direct Git process. A hook child may survive and continue touching the candidate. Commit therefore needs transaction-specific timeout classification and proof that synchronous descendants are quiescent before publishing. Arbitrarily daemonized hooks remain outside the guarantee.

## Index representations

- **Split index:** Let native Git and hooks read/write shared storage. Never flatten it or disable configured split-index behavior. Sidecars are content-addressed but shared-sidecar cleanup across linked worktrees is not covered by this worktree’s `index.lock`; recheck and real-Git proof are required.
- **Sparse index:** Raw seeding plus native Git should preserve logical outside entries and skip-worktree state. Expansion of representation is less serious than logical corruption, but no such change should be accepted without proof.
- **Linked worktrees:** Lock only the index returned for the current worktree. A writer to that same linked index must fail; writers to another worktree’s independent index may proceed. Shared refs remain outside this lock.
- **Unborn HEAD:** Capture the symbolic branch with no old OID. Native Git must create the initial ref, and the resulting commit must have no parent. A staged initial commit has a real index; a missing-index/no-staged-content case should remain rejected.

## Atomicity boundary

The transaction can realize:

- continuous exclusion of lock-respecting writers to this worktree index;
- under-lock validation of the reviewed index;
- readers seeing either the complete old or complete resulting index;
- preservation of synchronous hook-driven index edits;
- explicit uncertainty after irreversible boundaries.

It cannot provide a transaction spanning:

- native Git’s ref update and Rhizome’s later real-index rename;
- concurrent `update-ref`, symbolic HEAD edits, worktree editor writes, config edits, or direct index overwrites;
- detached/background hook processes;
- same-UID hostile filesystem replacement.

Native Git’s own ref lock prevents a lost ref update once it has selected an expected old OID, but it cannot bind that selection atomically to the coordinator’s earlier review. Postcondition inspection and uncertainty are therefore mandatory.

## Focused real-Git proofs before ungating

1. Existing writer-after-review test passes: competing same-worktree `git add` fails on `index.lock`, and the commit contains only reviewed content.
2. Writer wins immediately before lock acquisition: under-lock validation rejects and preserves the writer’s index exactly.
3. Hook success: pre-commit stages another path; commit and published index match native Git behavior.
4. Hook rejection after `git add`: HEAD unchanged, hook edits published, draft retained, activity failed.
5. Post-commit `git add`, nonzero post-commit exit, and a hanging post-commit hook: prove index publication and success/uncertainty distinctions.
6. Synthetic signing success and failure, without credentials; verify signer invocation and signed commit object on success.
7. Split, sparse, linked-worktree, and unborn initial commits, extending the existing patch proofs at `tests/test_patches.py:297-408`; assert commit trees, final index flags/storage, `write-tree`, and `status`.
8. Ref races using blocking hooks:
   - branch moved during pre-commit;
   - symbolic HEAD changed;
   - branch moved again during post-commit.
   None may be reported as an ordinary retryable failure.
9. Failure injection after ref advancement at candidate fsync, candidate-to-lock replacement, final rename, and directory fsync. Every such case must surface uncertainty and leave refreshable repository state.
10. Timeout proof demonstrating no surviving synchronous hook can continue modifying the candidate after publication or cleanup.