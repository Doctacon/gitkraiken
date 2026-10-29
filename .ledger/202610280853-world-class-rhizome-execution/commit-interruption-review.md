> Historical review: its later normal-return/pipe-EOF quiescence reasoning is
> superseded for the regular-file runner. See `commit-quiescence-review.md` and
> root implementation/evidence in `commit-root-followup.md`. This report is not
> current commit-enablement approval.

## Judgment

Yes. Given the constraint that Rhizome must not kill a running Git process, `timeout=None` for this private commit invocation plus fail-closed artifact retention is safer than inventing process-group termination or publishing a candidate whose writers may still be alive.

The trade-off is explicit: a broken hook or signer can block indefinitely. That is a liveness cost, not a data-integrity ambiguity, and the operation should remain visibly running and non-cancellable.

## Required corrections

1. **Preserve raw process termination status.**  
   The commit transaction cannot rely on current `_run_process()`: it converts every nonzero result, including a negative signal return, into `GitError` at `src/rhizome/git.py:228-269`. The private commit runner must retain the `CompletedProcess` and distinguish:
   - `returncode == 0`;
   - normal nonzero completion;
   - `returncode < 0`, meaning direct termination by signal;
   - an exception/interruption before a `CompletedProcess` is returned.

   This needs no public API expansion.

2. **Retention must be armed before launch.**  
   Once process creation is attempted, cleanup must default to disabled. Only a normal nonnegative return may establish candidate quiescence and permit publication or deletion. If control unwinds unexpectedly, `finally` must leave the owned lock and candidate intact.

3. **Do not use `TemporaryDirectory` for the candidate.**  
   Its context-manager/finalizer cleanup conflicts with intentional retention. Use an explicitly owned private directory whose deletion is performed only after a proven normal completion. Close descriptors normally, but leave:
   - the real `index.lock` pathname;
   - candidate index;
   - candidate `.lock`, if present;
   - the candidate directory.

   Never automatically break this retained lock on a later run.

4. **Report retained state clearly.**  
   A catchable interruption or negative return should become `GitOutcomeUncertain` and identify that the worktree index remains deliberately locked pending inspection. The reviewed plan remains consumed and the draft remains intact.

## Synchronous hook quiescence

A normal return from native Git is sufficient for the scoped guarantee:

- Git synchronously waits for `pre-commit`, `prepare-commit-msg`, `commit-msg`, and `post-commit`.
- It also waits for the configured signer command.
- `subprocess.run(capture_output=True)` does not return until Git exits and its captured pipes reach EOF.

Therefore a **normal nonnegative Git return** is adequate evidence that normal synchronous hooks/signing have completed. The stable candidate can then be inspected, fsynced, and published, including after a normal nonzero hook or signing rejection.

Pipe EOF is not independently a process-tree guarantee. A child can redirect or close inherited pipes and continue running. That is a background/detached hook, which is correctly outside the claimed contract.

## Outcome handling

- **Return 0:** validate the expected HEAD/ref postcondition, stabilize and publish the candidate, then report success or uncertainty according to the ref result.
- **Normal nonzero return:** stabilize and publish the candidate so synchronous hook-driven index edits persist; retain the draft and report ordinary failure if HEAD is unchanged, otherwise uncertainty.
- **Negative signal return:** do not inspect-and-publish or delete the candidate. Retain lock and directory; report uncertainty.
- **Execution interrupted without a `CompletedProcess`:** same retention rule. If the interruption cannot be converted into an application error because the interpreter itself is terminating, the pre-armed no-cleanup state still preserves the filesystem evidence.
- **Clearly pre-exec failure** such as Git not being found, where no child could have started, may clean up normally.

This is the soundest conservative boundary: normal native completion permits publication; unprovable quiescence leaves the repository intentionally locked rather than guessing.