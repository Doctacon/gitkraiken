## Decision

Keep commit coordinator/UI gated.

The smallest credible correction is a commit-private POSIX process group:

- Launch native Git with `process_group=0`, retaining the current session rather than using `setsid`.
- After a direct **nonnegative** Git return, require the dedicated group to become absent before reading final capture, publishing, or deleting the candidate.
- Probe only with signal `0`; never terminate the group, impose a timeout, or cancel Git.
- Preserve native Git, hooks, and signing unchanged. No public-interface expansion is warranted.

The current rule is unsound: `_CommitIndexTransaction.run()` waits only for direct Git and immediately consumes capture at `src/rhizome/git.py:502-524`. The real fault proves Git can return `0` while an ordinary foreground hook child remains active, after which Rhizome deletes/moves its candidate and the child’s late `git add` fails `128` (`commit-fault-evidence.md:20-23,56-58`; `tests/test_commit_faults.py:159-296`). The normal-return claim in `commit-interruption-review.md:35-43` is therefore obsolete. Regular-file capture has no pipe-EOF quiescence property, and ordinary helpers can redirect output anyway.

## Required semantics

For the dedicated PGID:

- `killpg(pgid, 0)` succeeds: group remains live; continue waiting.
- `EPERM`: existence/quiescence is unresolved; conservatively treat it as live.
- `EINTR`: retry.
- `ESRCH`: the inherited process group is absent; publication may proceed.
- Any other probe failure: retain lock/candidate and report uncertainty.

Negative direct returns and exceptional waits should continue to retain immediately; they need not wait for the group because they must not publish. Record the PGID alongside the existing Git PID and phases.

This covers descendants that ordinarily inherit Git’s process group, including helpers that redirect all standard streams. It deliberately does not claim control over children that call `setsid`, change process group, daemonize, or otherwise escape, nor over hostile same-UID actors. Signal `0` is an existence probe, not ownership or security.

Reject `setsid`: it would remove the controlling terminal and change session/SIGHUP behavior. `process_group=0` is narrower, although it still changes terminal job-control behavior.

## Important compatibility risk

A new process group remains in Rhizome’s session but will normally be a **background** terminal process group. A signer or hook reading `/dev/tty` may receive `SIGTTIN` or `EIO`; terminal-generated signals also continue to target Rhizome’s foreground group. This matters because signing remains Git-owned by contract (`docs/design/world-class-rhizome.md:24-26,356-360`), while current tests use only a noninteractive protocol double (`tests/test_commit_transactions.py:115-149`).

Do not add foreground-terminal handoff casually: `tcsetpgrp` would make the Textual app background during commit and introduces substantially more UI/signal complexity. If interactive TTY signing is required, this is an unresolved architectural blocker rather than justification for `setsid`.

## Spawn classification defect

`run()` explicitly acknowledges that interrupted creation may leave an unknown child (`git.py:503-505`) but then clears retention for **every** `Popen` `OSError` (`git.py:511-513`). That is too broad.

Keep retention armed across an unreturned `Popen` call. Only a failure proven to occur before process creation was attempted—for example, an explicit executable-resolution/preparation failure—may disarm retention. Exception type alone is insufficient. If construction is interrupted after spawn but before returning the process object, the safe result is retained uncertainty with an unobserved PID/PGID.

## Required public fault coverage

Through `Repository.execute(Action("commit", ...))` and real disposable Git:

1. Strengthen the existing foreground-child fault: while the child is active, execution must remain running, the candidate and real index must remain unchanged, and after release the late staging must succeed and be published.
2. Repeat with the child explicitly redirecting/closing stdout and stderr, excluding pipe EOF as the mechanism.
3. Cover normal nonzero completion as well as return `0`, because rejected hooks/signers also publish candidate edits.
4. Add the equivalent configured-signer fault: kill only the signer shell while its ordinary foreground child, with redirected streams, remains alive and performs a late candidate write.
5. Preserve signer success/rejection tests, and add real-PTY standalone plus tmux signer tests that expose `/dev/tty` and foreground-PGID behavior on macOS and Linux.
6. Add paired launch faults:
   - demonstrably pre-spawn failure cleans normally;
   - real child spawned, then `Popen` construction boundary raises `OSError`: uncertainty, unchanged real index, retained lock/candidate, no kill.
7. Exercise `EPERM` as non-quiescent and retain the existing negative-signal/interrupted-wait proofs.

## Unresolved blockers

- Interactive signer/controlling-TTY compatibility under `process_group=0`.
- Darwin and Linux/WSL evidence, including current and minimum Git, for process-group behavior.
- Zombies can keep a group visible after writers have exited; no portable zero-signal probe distinguishes them. Safe behavior is an indefinite running operation, consistent with the accepted hook/signer liveness cost.
- PGID reuse or deliberate same-UID group manipulation can cause false waiting and is not a durable ownership mechanism. It does not justify premature publication or a broader control claim.
- The existing red remains unresolved; no acceptance or ungating claim is supportable.