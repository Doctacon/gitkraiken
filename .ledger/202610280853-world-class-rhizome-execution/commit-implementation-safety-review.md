## Judgment

Keep commit gated. The core reviewed-index design is sound, and the current-host representation results are meaningful, but two concrete source defects and several high-value fault proofs remain.

## Concrete source blockers

1. **The private commit runner can still kill Git on interruption and captures unbounded output.**  
   `Repository._commit_reviewed()` uses `_git(... timeout=None, ok_codes=None)` at `src/rhizome/git.py:871-879`, but `_run_process()` still calls `subprocess.run(capture_output=True)` at `git.py:242-283`. CPython’s `run()` kills the direct child when `communicate()` raises, including exceptional interruption despite `timeout=None`. This contradicts the comment at `git.py:872-873` and C9’s non-cancellable-mutation rule (`world-class-rhizome.md:342-346`).

   `capture_output=True` also accumulates all native summary and hook/signer output before `_raise_process_error()` clips it at `git.py:228-231`. A noisy trusted hook can exhaust memory.

   **Smallest correction:** use a commit-private `Popen` wait path that never kills after successful spawn, preserves raw negative/nonnegative status, and drains stdout/stderr while retaining only bounded output. Parse/count native OID headers incrementally so truncation cannot hide a second candidate. Do not generalize this into a public environment/process API.

2. **Spawn state is currently conflated with “a child may be running.”**  
   `transaction.started = True` is set before `_git()` at `git.py:871`. Consequently, a proven pre-exec `FileNotFoundError`/`OSError` from `git.py:273-280` is converted by `__exit__()` into retained-lock uncertainty at `git.py:545-575`, even though no child exists. That is safe but dishonest failure classification.

   The private runner needs to distinguish:
   - spawn definitely failed: ordinary failure and cleanup;
   - child spawned but waiting was interrupted: retain lock/candidate and report uncertainty;
   - normal negative return: retain;
   - normal nonnegative return: candidate may be stabilized and published.

3. **Alternate-lock detection must test pathname existence, not target existence.**  
   `candidate/index.lock.exists()` at `git.py:492` misses a dangling symlink. Any directory entry at that lock pathname means the candidate is not demonstrably stable. Use a no-follow `lstat`/fd-relative existence check and fail uncertain without publication.

4. **Retained recovery context is insufficient for interrupted and nonzero/ref-moved cases.**  
   `record()` stores only the Rhizome PID and original ref state (`git.py:449-475`). It does not record the spawned Git PID, normal return code, publication phase, or observed post-run HEAD/symbolic state. `record_created()` records an OID only when a unique success header was parsed (`git.py:477-488, 887-896`).

   For return-zero mismatches, the created OID is in fact recorded before publication, which is correct. But `tests/test_commit_transactions.py:175-189` never proves that the retained `context.txt` contains it. For a normal nonzero after a ref moved, there may be no native summary at all; pinned Git can fail after ref update, and Rhizome must not invent a “created OID.” Persist the honest observations instead: PID, return status, parsed OID or explicitly unverified, observed HEAD/symbolic state, and whether publication had begun/completed.

## Source behavior that is already sound

- Real worktree index parent identity and owned lock inode are pinned and repeatedly checked at `git.py:398-417`.
- The candidate is opened no-follow, required to be regular, Git-validated and fsynced at `git.py:490-507`.
- Candidate-to-lock replacement is followed by an inode ownership check before final publication at `git.py:534-541`. This is adequate within the expressly limited guarantee against lock-respecting writers; arbitrary same-UID pathname replacement remains outside scope.
- Under-lock reviewed-state revalidation and staged checks occur at `git.py:853-869`.
- Normal nonzero hook/signing rejection publishes synchronous candidate edits and then returns ordinary `GitError` when HEAD is unchanged (`git.py:894-925`). Existing tests at `tests/test_commit_transactions.py:82-147` support this.
- Return-zero identity checks bind the pinned native summary OID to the raw object, exact parent list, final HEAD and symbolic target (`git.py:887-921`). The same-parent substitution regression is correctly uncertain.
- Failure after the final rename but before directory fsync is routed through unsettled transaction handling and therefore uncertainty, not rollback.

## Native Git behavior, not Rhizome regressions

- Hooks/signers may hang indefinitely. That is the accepted liveness cost of keeping them authoritative.
- A post-commit hook may move refs; Rhizome cannot make the native ref update and later real-index publication jointly atomic.
- A detached/background hook that deliberately outlives native Git is outside the synchronous-quiescence guarantee.
- Git may return nonzero after state changed, or return zero while a post-commit hook substituted the ref. These require uncertainty, not rollback.
- Split-index sidecars are shared Git storage and cannot be made atomic against arbitrary concurrent same-UID deletion.

## Missing fault-injection evidence

The 44 current-host passes do not cover these gates:

1. **Exceptional wait without kill:** block a real hook, inject an exception in the wait path, prove Git remains alive, the old real index remains readable, and the owned lock/candidate remain retained. Release and reap the child without automatic publication.
2. **Negative direct signal:** prove uncertainty and retention; do not publish merely because the direct process has exited.
3. **Bounded output:** large success and rejection output must neither deadlock nor grow retained memory without bound; OID parsing and truncation reporting must remain correct.
4. **Native nonzero after ref movement:** use real Git to force a post-ref-update failure; assert uncertainty, published stable candidate, and context that records observed facts without falsely claiming a native OID.
5. **Ref races:** pre-commit branch movement/CAS failure, symbolic-HEAD change, and post-commit movement. Ordinary failure is permitted only for normal nonzero with observed unchanged HEAD/symbolic state.
6. **Publication boundaries:** inject failures at candidate fsync, split-sidecar fsync and parent fsync, candidate-to-lock rename, post-rename ownership verification, final rename, and final directory fsync. Assert exact old/published index state, lock ownership, retained artifacts, and OID context.
7. **Ownership:** prove a pre-existing foreign lock is inode/byte unchanged, owned-lock substitution is detected, and a dangling alternate `index.lock` blocks publication.
8. **Retained mismatch context:** extend `test_same_parent_ref_substitution...` to assert the retained context contains the actual native-created OID and truthful publication state.
9. **Split storage:** current `tests/test_commit_representations.py:192-279` strongly covers forced sidecar replacement and expiry on the host, but linked-worktree and split-index behavior are only tested separately. One linked-worktree split-index case is warranted because administrative path resolution and sidecar placement interact.
10. **Git 2.36 runtime:** pinned source supports the design, but `evidence.md:75` explicitly says the minimum-version run was not acceptance evidence. Run the scoped transaction/representation proofs on actual Git 2.36 and current Git, especially native-summary parsing and forced split-sidecar replacement.

The sparse, unborn, detached, SHA-256 and ordinary linked-worktree proofs in `tests/test_commit_representations.py` are useful evidence, not defects. No representation-specific downgrade is justified.