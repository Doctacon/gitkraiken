# Native commit fault evidence

Date: 2026-10-28

## Scope

Owned changes remain limited to `tests/test_commit_faults.py` and this commit-fault ledger evidence/result. Every commit attempt enters through public `Repository.execute(Action("commit", ...))` against a disposable real Git repository. Faults and observations use only real hooks plus OS/subprocess boundaries. No internal `Repository` or coordinator mock, coordinator/UI bypass, product-code edit, network/provider operation, user-root operation, installation, publication, or project commit was used.

The prior builder's launch, direct-signal, native-output, and ref-movement tests remain in the file. The root agent reported that its missing-context red was fixed and that eight fault/process tests were green; those eight tests were not rerun in this bounded investigation.

## Foreground synchronous descendant investigation

Added `test_foreground_hook_child_blocks_publication_after_hook_shell_is_killed`.

Fixture sequence:

1. A real `post-commit` hook shell records its PID and invokes a child shell in the foreground, with no backgrounding or detachment.
2. The child records its PID and inherited `GIT_INDEX_FILE`, then signals readiness and waits at a filesystem gate **before** running Git or taking `<candidate>.lock`.
3. The test proves the child and candidate are live and that no candidate lock exists, then sends `SIGKILL` only to the disposable hook shell.
4. A `subprocess.Popen.wait` boundary observer records native Git's direct completion. On this host Git normalized/ignored the post-commit hook signal and returned `0` while the foreground child remained alive and gated.
5. While the child was still active, the test gave `Repository.execute` one second to settle and captured candidate and real-index state.
6. In `finally`, the test released the child gate, observed its explicit post-staging status/completion markers, and joined the `Repository.execute` worker. It did not kill the child to manufacture quiescence.
7. After release, the child attempts `git add -- late-hook.txt` against its inherited candidate. A safe retained-uncertainty path must preserve that staging in the retained candidate; a genuinely quiescent publication path must preserve it in the real index.

## Reproducible red

Command:

```text
uv run --locked pytest -q tests/test_commit_faults.py::test_foreground_hook_child_blocks_publication_after_hook_shell_is_killed
```

Result:

```text
1 failed in 1.66s
AssertionError: Rhizome deleted or moved the candidate while its writer was active
assert False
```

Passing preconditions before that assertion establish that:

- the foreground child reached its gate before taking a candidate lock;
- only the hook shell received `SIGKILL`;
- the foreground child was still alive;
- native Git directly exited while that child was active;
- native Git's observed direct return code was exactly `0`;
- `finally` released the child, observed its post-staging status/completion markers without killing it, and observed `Repository.execute` settle.

The inherited candidate path was:

```text
<disposable worktree>/.git/rhizome-commit-5eqmttl9/index
```

It no longer existed at the active-child observation. After the mandatory gate release, the child's recorded `git add` status was `128`, confirming its late staging did not survive the early candidate move/deletion.

This reproduces the regular-file-capture gap described by the request: direct native-Git exit is not proof that an ordinary foreground hook descendant is quiescent after the hook shell itself is killed. Rhizome currently reaches candidate publication/cleanup while that inherited candidate writer is still active.

## Stop point and limitations

Stopped at this first reproducible red. No publication fsync, rename, or ownership fault case was added or run. No existing broader fault/process suite, linter, or full project suite was run in this investigation. No product fix was attempted and no safety assertion was weakened.

After the red, the child-cleanup oracle was narrowed from `kill(pid, 0)` disappearance to the child's explicit post-staging status/completion markers: an exited orphan can remain visible as a zombie, so PID visibility is not a portable completion oracle. Per the stop instruction, the focused test was not rerun after that cleanup-only edit; the active-child, direct-return, candidate-retention, and late-staging assertions are unchanged.

The proof is current-host behavior (Darwin, native Git as configured by the test environment). It does not claim how every Git/platform combination reports a killed post-commit shell; the test explicitly requires and records the direct `0` return before evaluating Rhizome's behavior.
