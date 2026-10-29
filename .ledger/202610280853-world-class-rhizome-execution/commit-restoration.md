# Restore the ordinary commit workflow

> Historical restoration report. The operator has since resumed the roadmap
> while keeping crash/retry deferred; see `composer-progress.md` for current scope.

## Scope reset

The operator explicitly narrowed work to getting Rhizome's normal commit workflow
working again. Installer work and the larger M1–M4 roadmap are deferred. No new
installer supervisor/prerequisite was approved. Preserve existing uncommitted
work; no project commit, publication, user-owned installation, or provider write.

## Reproduction

On resumption the existing public tests failed unchanged:

```
uv run --locked pytest tests/test_commit_terminal.py \
  tests/test_operations.py::test_commit_excludes_an_external_index_writer_after_review \
  tests/test_precision_workspace.py::test_narrow_mouse_and_keyboard_inspect_stage_commit_flow \
  -q --tb=short
3 failed in 8.17s
```

The real PTY native twin succeeded; Rhizome's new Git process group stopped on a
normal `/dev/tty` hook read (`T`, `T`). The other failures were the explicit
coordinator/UI gates. Fixture cleanup resumed and observed Git, not killed it.

## Bounded repair

- Keep the real index lock, native alternate index, under-lock review validation,
  process-group quiescence, and uncertain-outcome retention.
- When the caller owns a controlling terminal, use a tiny isolated invocation of
  the application's existing Python interpreter to hand the foreground to the
  new process group **before execing Git**. This preserves Git's PID/group and
  avoids a threaded-process `preexec_fn` or a race after Git starts its hooks.
  No separate executable/dependency is installed.
- Restore foreground ownership and terminal modes on exit, using a thread-local
  SIGTTOU mask rather than changing the UI's signal handlers. Background callers
  do not steal the foreground. In-process terminal leases are serialized.
- Spool Git output as before. While interactive Git runs, tail stderr to the
  terminal so a hook's visible prompt is not hidden. Periodic wait polling has
  **no overall timeout, cancellation, termination, or replay**. Regular spools do
  not impose a pipe lifetime or signal a late writer when the parent stops reading.
- The real Textual app deliberately suspends input/rendering and runs the commit
  synchronously during this native-terminal handoff. No UI waiter can resume the
  app over a live mutation. Textual 1.x resumes only on normal context exit, so
  native exceptions are captured inside suspension and reported after resuming.
- Remove the coordinator and Commit-button gate; route through the same reviewed
  transaction, not a direct-mutation bypass. Disable the control while busy,
  without staged files, or during conflicts/in-progress integration.
- Stop stripping accepted composer text. Blank admission still uses `strip()`,
  but Git receives the original text. Existing `git commit -m` cleanup semantics
  remain Git-owned. Automatic templates/editor-mode composition remain deferred,
  not a new prerequisite for ordinary staged commits.
- README, usage, and Help now describe the native-terminal handoff, draft retention,
  and uncertain-outcome inspection rather than a permanently unavailable Commit.

## Validation so far

- Original PTY plus process/fault cases: **14 passed in 16.95 s** after foreground
  handoff.
- Added stderr-prompt PTY case: **1 failed in 11.17 s** because only the native
  twin's prompt was visible. Fixture released the hidden read without killing
  Git. After regular-spool diagnostic forwarding, terminal/process/fault/
  publication slice: **22 passed in 24.72 s**.
- Unchanged competing-writer coordinator, routine stage/unstage/commit, narrow
  Pilot core flow, rejected-hook/draft/reopen/success, and legacy stage/commit
  tests: **5 passed in 30.08 s**.
- Added real SSH-signing PTY native twin: **1 passed in 2.17 s** on current Git.
  `ssh-keygen` generates a disposable encrypted Ed25519 key. No user key, agent,
  device, config, or network is used. Both native and Rhizome commits prompt for
  the fixture passphrase; exact resulting OIDs match and `git verify-commit`
  verifies both signatures against a fixture-only allowed-signers file. This is
  real SSH signing, not GPG/hardware/all-provider acceptance.
- Actual Git 2.36 transaction/representation/process/fault/publication/PTY/
  coordinator/precision/draft batch (`task-29`): **89 passed in 84.83 s**.
  See `commit-restoration-git236.txt`. This collection predates the extra SSH case.
- All three PTY cases on actual Git 2.36: **3 passed in 3.29 s**.
- `scripts/commit_smoke.py` passes on current Git and actual Git 2.36. It uses an
  isolated tmux server and a real, non-headless Textual driver at 80×24; Pilot
  reviews a file, stages one replacement, submits an exact multiline message,
  survives an interactive hook rejection, opens/closes Activity, and commits on
  the second attempt. The tmux input goes to the native hook, not Pilot. Raw commit
  bytes preserve the message under configured `commit.cleanup=verbatim`; the
  other unstaged replacement and real index remain correct. The UI resumes after
  both outcomes; owned transaction artifacts are removed on settled completion.
- Two early smoke attempts were **fixture issues**, not behavioral red evidence:
  macOS `/var` was a symlink rejected by private state storage (use the physical
  fixture path), and a status assertion expected the hook's second output line
  even though the UI displays the first line. The corrected oracle additionally
  proves both actual answers equal `approve` and the intended hook refusal ran.
- `uv run --locked ruff check src tests scripts/commit_smoke.py`, `git diff --check`,
  and `sh -n scripts/install.sh` passed. No installer product edits were made.

Before this scope reset, root added candidate-fsync, candidate-move, final-index-
replace, and post-replacement directory-fsync publication fault proofs one at a
time: **1 / 2 / 3 / 4 passed** respectively. Existing retention behavior already
satisfied them; no production change was required. They are included in current
publication coverage, not claimed as newly repaired defects.

## Broad result and limitations

Fresh full-suite discovery (`task-28`, `commit-restoration-full-suite.txt`):
**697 passed, 2 failed in 1010.28 s**. All collected commit/coordinator/UI cases
passed. The two failures were:

1. `test_config_is_printed_without_repository_or_config_mutation`: an old fixture
   assumed `tmp_path` was empty before the command, but Git-isolation fixtures now
   prepare HOME/config directories there. Independently reproduced **1 failed in
   0.53 s**. The test now snapshots the initial directory tree and requires exact
   preservation with no files created, rather than mistaking setup for CLI writes.
   No product change or weakened no-mutation oracle.
2. `test_killed_intermediate_launcher_retains_activity_until_child_exits`: the
   already-known installer defect. It remains unfixed, unskipped, and deferred.

The full collection predates the additional SSH case and the Help copy update.
Final targeted configuration/PTY rerun: **5 passed, 116 deselected in 6.20 s**;
full source/tests/smoke Ruff, whitespace checks, and installer shell syntax passed.
There has **not** been a fully green changed full-suite run. No installer fix or
release acceptance is implied.

This restores/proves the ordinary local workflow, not the entire roadmap,
published installation, all signing devices, human usability, or cross-platform
release acceptance. The real-terminal smoke is automated Pilot on a real terminal,
not a human participant study or a full physical-keyboard/mouse matrix.
