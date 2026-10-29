# Native commit process and index-flag follow-up

## Scope and gates

Public `Repository.execute(Action("commit", ...))` in real disposable repositories; subprocess/filesystem boundaries only. Coordinator and UI commit remain gated. No commit in the project, publication, provider mutation or installation occurred.

## Bounded capture and interruption

- A real pre-commit hook emitted 16 MiB on stderr and refused the commit. Initial process-capture red: **1 failed in 26.63s**, with **33,709,527 traced Python bytes** allocated. HEAD/index/worktree preservation and diagnostic truncation assertions passed before the memory assertion.
- `_CommitIndexTransaction.run` now uses private `Popen` with regular temporary output files, no deadline, no implicit context-manager wait and no kill. Only bounded stdout/stderr prefixes enter retained Python output. All native-shaped stdout headers are counted incrementally, including beyond the retained display prefix. Source-side synchronous hook/signing authority is unchanged.
- Memory regression plus real competing writer: **2 passed in 27.46s**. The process intercept moved from `subprocess.run` to `Popen` in both writer tests; their success/data assertions were not weakened. The coordinator test remains blocked by its intentional gate.
- Adding a loose ten-second catastrophic-scanning guard exposed a second defect: **1 failed in 27.54s**, operation elapsed **26.77s**. The unanchored URL scheme regex retried on every alphabetic character of a 64-KiB diagnostic.
- URL matching now starts once per maximal ASCII scheme-shaped token while preserving numeric/punctuation prefixes. The fixture explicitly checks `0https://...` remains correctly redacted. Green: **1 passed in 1.99s**, Ruff passed.
- A real pre-commit hook stages an additional file and waits on a test-owned release file. Injecting an exception at the native process wait proves Git stays alive, the original real index remains byte-identical, and lock/candidate are retained. The fixture always releases and reaps Git rather than killing it.
- That test first exposed missing Git-PID recovery context: **1 failed in 1.37s**. Native PID, spawn phase and observed return status are now appended durably. Both process tests: **2 passed in 2.79s**, Ruff passed.

The memory/time assertions are structural regression guards, **not** application memory budgets, p95 measurements, real-terminal responsiveness or acceptance evidence. Temporary output storage may grow on disk while native Git runs; this change does not establish a disk-space bound. Deliberately detached hooks are outside synchronous quiescence.

## Minimum Git and combined tests

- A disposable upstream Git 2.36.0 build initially used the wrong-case `TEMPLATE_DIR` Make variable. Missing default hooks directories caused fixture failures in tasks 17/18, not behavioral failures. The correct lowercase `template_dir` was compiled in task 19; a separate disposable init verified its hooks directory. No `make install` occurred.
- Task 20, actual Git 2.36.0: **44 passed in 15.24s** across patch/commit/representation tests.
- Task 21, after runner/PID/redaction work: **46 passed in 33.40s** on host Git 2.50.1, then **46 passed in 15.03s** on actual Git 2.36.0.
- These runs predate the following index-flag fix and are not full-suite or UI acceptance.

## Intent-to-add review identity

An external Git writer changes a staged empty file to intent-to-add immediately before Rhizome acquires the real index lock. `ls-files --stage -z` remains identical, yet native commit omits that formerly reviewed file. The public regression failed with no expected refusal: **1 failed in 1.01s**.

`Repository._commit_index_fingerprint` now streams SHA-256 over the complete no-follow regular index, preserving flags/extensions and the actual representation rather than normalizing it. The private commit compares this fingerprint again under the real lock. Intent-to-add and the two competing-writer cases: **3 passed in 2.24s**, Ruff passed.

The test's final preservation oracle uses native porcelain status for intent-to-add; plain `git diff --cached` intentionally has special intent-to-add display semantics documented by Git and is not the commit-tree oracle.

**Coordinator planning still needs this representation-sensitive binding and commit configuration capture**, plus the under-lock callback integration. Optional status/diff index writes must not create self-induced stale comparisons. Do not remove either commit gate yet.

## Remaining work

See `commit-implementation-safety-review.md`: definite launch failure; negative signals; duplicate summary after truncation; dangling alternate lock; ref-moved nonzero completion; publication/fsync/ownership failures; observed post-run refs/publication phases in retained context; mismatch OID assertion; linked + split interaction; templates/cleanup/multiline composition and UI lifecycle. Fetch is separately gated pending integration of the completed backend repair.
