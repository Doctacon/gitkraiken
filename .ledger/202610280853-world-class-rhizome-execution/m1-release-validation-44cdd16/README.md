# M1 release validation: 44cdd16

Candidate: `44cdd1641df92e4084690cbd7288ad9d34e38d92`, branch
`feat/hardening-pass`, clean at entry. Started 2026-10-29 20:05 UTC under the
operator's 15-minute increment rule. No product/test files changed, no publication,
no live user installation changes, and no invented platform/human results.

## Complete local collection: not green

The bounded orchestrator collected and ran all **746 cases exactly once** in four
disjoint processes. JUnit case identities match collection: no missing, duplicate
or unexpected cases; candidate inputs/HEAD stayed unchanged. Runtime: **390.78 s**.
Result: **745 passed, one failed**, not a release pass. Raw evidence is the
partition logs/XML, collection, partitions, candidate-inputs and summary files.

Failure:
`tests/test_refresh.py::test_remote_change_during_automatic_read_discards_and_retries`.
Its provider double renames `origin` to `renamed` every time it is called. The failed
run attempted that rename again during automatic retry and got Git exit 2 rather
than the transient expected “Remotes changed” status. This points to timer-sensitive
test injection, but product versus test responsibility needs verification; do not
silently waive the failure. An isolated refresh-module rerun is recorded separately
in `refresh-isolation.txt`; a green rerun does not erase the original failure.

## Static and artifact checks

Root Ruff lint, whole-tree formatting (103 files), system sh/dash syntax and
`git diff --check` pass. An exact `git archive` of the candidate built wheel/sdist
offline. The wheel contains the stylesheet, controls and diagnostics and passes
an isolated-home import/CLI-help smoke using existing development dependencies.
This is not a managed Python/uv installation lifecycle test. Temporary artifacts
were removed; sizes, SHA-256 hashes and contents are in `packaging.json`.
The source distribution includes tracked `.ledger` records; the wheel does not.
No decision to publish that source distribution has been made.

## Hosted and pinned-installer checks: blocked on remote availability

Read-only GitHub checks found no CI runs for this commit, and the commit API reports
HTTP 422 / “No commit found for SHA”. Exact commands/results are recorded in
`github-readiness.json`. Testing this exact revision through hosted CI and the
GitHub-pinned bootstrap requires uploading the branch first. No push, workflow
write, release tag or publication was performed or inferred from validation scope.

## Next bounded step

Diagnose/stabilize the refresh regression without weakening its stale-read/retry
assertions. Obtain explicit authorization before pushing `feat/hardening-pass` to
`origin` for CI and remote-pinned installer validation. Other platform, performance
and human-pilot evidence remains pending; startup timing is already accepted.
