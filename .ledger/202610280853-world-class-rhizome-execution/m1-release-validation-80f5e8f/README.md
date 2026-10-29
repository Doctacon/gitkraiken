# M1 release validation follow-up: 80f5e8f

Started 2026-10-29 20:22 UTC, following the first bounded validation checkpoint.
The operator confirmed the existing headless app / real temporary Git / provider
boundary and authorized focused validation-fix commits plus branch pushes, but no
merge, tag, release or live user installation changes.

## Stabilization and candidate

The original `44cdd16` run remains recorded with 745 passes and one refresh failure.
Its refresh module passed 12 cases in isolation (47.72 s), establishing timing
sensitivity rather than erasing the failed full-collection evidence.

The test explicitly drives poll phases but also allowed the independent two-second
UI timer to run. After a remote changed, the correctly immediate retry could invoke
the rename-injecting provider again before the test inspected the discarded result.
The fixture now sets its existing configurable polling interval to 3,600 seconds;
explicit poll calls and every stale-result/loading/status/retry assertion remain
unchanged. No production behavior changed. The entire refresh module passed again
before commit; root lint/format/whitespace checks passed.

Commit `80f5e8f7586ee7e3630b0dbc43aeef00b26eed95` contains only that fixture change
and the outdated installer-deferral CI label/comment cleanup. It was pushed without
force to `origin/feat/hardening-pass`. Validation artifacts/task notes are left
uncommitted, separate from the focused test fix.

## Fresh complete collection

**746 passed**, run once each in four disjoint partitions. JUnit identities match
collection exactly: no missing, duplicate, unexpected, failed or skipped cases.
Inputs and HEAD remained unchanged. Total wall time: **352.9 seconds**. This is
complete local partitioned coverage, not a claim of hosted/platform acceptance.
See `summary.json`, `candidate-inputs.json`, partition logs/XML and `collection.txt`.

## Hosted checks and remaining work

GitHub CI run: https://github.com/Doctacon/rhizome/actions/runs/40949349588
Latest observed state: in progress; see `github-runs.json`.

The preceding candidate's exact-archive build and wheel import/help smoke passed;
this follow-up changes tests/CI metadata only, not runtime/package files. Real
pinned-bootstrap install/update/rollback/uninstall, hosted results and the remaining
platform/performance/human evidence are still separate checks, not inferred passes.
