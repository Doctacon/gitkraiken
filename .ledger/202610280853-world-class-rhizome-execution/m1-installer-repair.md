# Installer crash/retry repair — 2026-10-29

## Authorization and scope

The operator authorized this formerly deferred repair, retaining increments under
15 minutes. Work began at 18:38 UTC on `ea6df96` plus the preceding formatting and
acceptance-document changes. No commits, publication, live user installation,
network downloads or M2 work. Existing installer tests/fixtures were reused without
changing their assertions or behavior.

## Reproduction and cause

```sh
uv run --locked pytest tests/test_install.py::test_killed_intermediate_launcher_retains_activity_until_child_exits -q --tb=short -p no:cacheprovider
```

Before repair: **1 failed in 3.39 s**; retry returned 0 while an intermediate
launcher's child survived SIGKILL of its parent. The existing short regression
also checks staging identity, ownership marker, unchanged current/previous pointers,
and absence of new command activity. Earlier red logs remain retained.

Considered wrapper marker removal, owner cleanup, and stale-lock reaping. The
causal fault was `guarded_command` clearing its activity marker unconditionally on
command return. Top-level cleanup then removed staging/lock state, allowing retry
while the delayed child still owned its original paths.

## Repair

`scripts/install.sh` retains activity for shell statuses greater than 128. POSIX
shells use these for signal deaths; intentionally high exit codes cannot be
distinguished and are conservatively fenced too. The wrapper disables its EXIT
trap before returning this status. Existing owner cleanup preserves marked state,
and lock acquisition refuses install/rollback/uninstall while activity is present.
Statuses at or below 128 retain existing ordinary-completion/failure behavior.

This is not general descendant supervision. A command that intentionally detaches
work and returns normally is outside this change's guarantee. The retained fence
requires manual inspection and does not expire automatically after children exit.
The existing regression's historical name says “until child exits”; its assertions
verify refusal while a child survives, not automatic post-exit recovery. The
installation guide now makes the persistent/manual behavior explicit.

A focused read-only consultant review found no scoped blocker and independently
identified these same limits; that review is reasoning, not test evidence.

## Validation

- Original regression + normal failure/retry lifecycle + surviving foreground
  descendant + signaled wrapper: **4 passed in 8.00 s**.
- `uv run --locked pytest tests/test_install.py tests/test_cli.py -q --tb=short -p no:cacheprovider`:
  **45 passed in 42.21 s** (37 installer + 8 CLI/distribution cases).
  Raw: `m1-installer-repair-tests.txt`.
- Existing installer module with a temporary `sh -> /bin/dash` PATH shim:
  **37 passed in 39.83 s**. Raw: `m1-installer-repair-dash.txt`. The shim and all
  fixtures use disposable paths; this is local macOS shell coverage, not Linux CI.
- `sh -n scripts/install.sh`, `/bin/dash -n scripts/install.sh`,
  `ruff check .`, `ruff format --check .` (103 files), `git diff --check`: pass.
- One initial batch mistakenly named nonexistent `tests/test_distribution.py`;
  pytest exited 4 with no tests run. Preserved in
  `m1-installer-repair-tests-command-error.txt`; corrected to `tests/test_cli.py`.

The historical 745-pass/one-failure partition record is not rewritten into a fresh
all-green suite. The installer failure is now resolved locally; hosted CI, live
installer/platform checks, other performance evidence and the human pilot remain
separate acceptance work. Startup acceptance and earlier formatter cleanup stand.
