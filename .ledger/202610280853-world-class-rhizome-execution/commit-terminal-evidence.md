# Native commit controlling-terminal evidence

## Scope

Added one regression in `tests/test_commit_terminal.py`. It enters the component only through public `Repository.execute(Action("commit", ...))` and compares it with a native `git commit` twin. Both repositories and all Git configuration are disposable and fixture-local. No coordinator/UI gate was changed or bypassed, no product code was changed, and no project commit, publication, dependency operation, credential signing, or user configuration was used.

This is real POSIX controlling-PTY and ordinary hook evidence. It is not actual signing, coordinator/UI acceptance, or an operator-terminal test.

## Fixture and oracle

The test creates a new PTY, forks a session leader, claims the slave as that disposable session's controlling terminal, and sets the fixture controller process group as foreground. It never changes the foreground group of the pytest runner's terminal or an enclosing tmux session.

Two equivalent repositories contain the same staged edit and executable `pre-commit` hook. The hook writes its prompt to `/dev/tty`, reads an approval from `/dev/tty`, verifies the answer, and records it outside the worktree. Both commit launches have stdin set to `/dev/null`; successful input therefore proves controlling-terminal access rather than inherited stdin access.

The native Git twin runs first in the fixture's foreground process group. The PTY driver sees its prompt, supplies `approve`, and native Git completes normally. The product worker then calls public `Repository.execute(Action("commit", message="terminal hook"))`. A subprocess-boundary observer records the PID of the real Git launched with `process_group=0`; it does not replace Git or the hook.

The fixture polls `ps -axo pgid=,stat=` for that recorded process group under a ten-second bound. On the current product it observes two stopped members (`T`, `T`) after the normal hook reaches `/dev/tty`. This is explicit stopped-group evidence, not a timeout inference.

Cleanup does not kill the hook, Git, or worker. The fixture controller transfers only its private PTY foreground to the stopped Git group, sends `SIGCONT`, and lets the already queued approval release the hook normally. It then reaps the product worker, restores its private PTY foreground group, and proves the Git group is absent. Product Git returns zero. Both hook answers are recorded as `approve`; both final HEADs are the same exact OID, logical indexes match, staged and worktree content are `reviewed\n`, and both worktrees are clean. These checks all pass before the deliberate compatibility assertion reports the stop.

## Focused command and first behavioral red

Exact command (existing environment; no install or download):

```text
.venv/bin/pytest -q tests/test_commit_terminal.py
```

Exact result summary:

```text
F                                                                        [100%]
FAILED tests/test_commit_terminal.py::test_public_commit_hook_can_read_the_controlling_terminal
1 failed in 2.86s
```

Material failure:

```text
AssertionError: Repository's Git process group lost foreground access to its controlling PTY and was stopped while the native Git twin was not
stopped_states: ['T', 'T']
handoff_performed: True
worker_status: 0
restored_foreground: True
remaining_group_states: []
product_outcome.kind: success
product_outcome.native_returncode: 0
```

The observed final commit was `cfe443ecdc374fe5c6d4eda460421e52d7bd1929` in both disposable repositories. The exact OID is fixture-date-specific and is not itself the compatibility oracle.

Per the stop instruction, no other tests, C3, linter, or full suite were run after this first genuine behavioral red. No product fix or ungating was attempted.

## Post-red fixture cleanup hardening

After the recorded run, pair review identified that the parent-side 35-second timeout closed the PTY and failed immediately without first releasing and reaping a still-live fixture controller. The timeout path now keeps the private PTY open, writes release input, and creates an abort marker. The session-owning controller polls that marker, waits until it has either reaped the worker or learned the real product Git group, performs the same foreground handoff plus `SIGCONT`, and observes worker completion. The parent continues servicing prompts and does not close the PTY or report its bounded-observation failure until it has reaped the controller. Native Git is now launched with `Popen` and polled as well, so the abort path can release and observe it rather than remaining hidden inside `subprocess.run`.

The normal stopped-group cleanup also now waits to observe a delayed PID/worker and, after recording a bounded completion failure, reaps the worker rather than exiting with pending fixture children. It still never kills a child or treats timeout as quiescence/success.

This was cleanup-only hardening after the mandated first behavioral red. The focused test was not rerun; therefore the exact result above is evidence from immediately before this hardening, while the `/dev/tty` compatibility assertion and normal red-path foreground handoff are unchanged.

## Conclusion and limitations

`process_group=0` places Repository's real Git and normal synchronous hook in a new background process group while the calling process remains the PTY foreground group. A normal `/dev/tty` read consequently stops the Git group with job-control state `T`; native Git in the caller's foreground group reads the same prompt/input without a stop. The fixture cleanup demonstrates that foreground handoff plus `SIGCONT` allows the pending hook and public commit to complete with the correct final repository state, but that recovery is test-owned and does not make the original product launch compatible.

This result is a component-level regression on the current POSIX host and Git. It does not exercise actual GPG/SSH signing, credentials, UI interaction, coordinator policy, Windows, or detached/background hooks.
