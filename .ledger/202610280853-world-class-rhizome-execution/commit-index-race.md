# Ordinary commit index race — currently gated

A new real-Git coordinator regression exposed the same lock-acquisition gap that
was already closed for selected patches. At the external `git commit` subprocess
boundary, a competing real `git add` succeeds after plan revalidation but before
Git owns its index lock. The ordinary commit can therefore include unreviewed index
content.

Reproduction before gating:

```text
uv run --locked pytest tests/test_operations.py::test_commit_excludes_an_external_index_writer_after_review -q --tb=short
1 failed in 0.71 s
competing git add returncode == 0 (expected exclusion on index.lock)
```

Only the subprocess boundary is wrapped; the competing writer and commit are real
Git in a disposable repository. No project/user repository is mutated by the test.

`OperationCoordinator._availability` now temporarily refuses `commit` with an
explicit reviewed-index-transaction explanation. No compatibility bypass is allowed.
This intentionally invalidates the previously green core commit-flow slice until a
safe implementation is proven. The low-level Repository fixture/oracle operations
are not the new UI authority and remain available to tests.

## In-progress private transaction (not enabled in coordinator/UI)

`commit-transaction-review.md` recommends native Git against an alternate index
under a continuously owned real index lock. `commit-interruption-review.md` supports
no commit deadline/automatic kill, raw process-status classification, and retention
of lock/candidate on unprovable interruption. These are architectural judgments,
not validation evidence.

An initial private `_CommitIndexTransaction` and `Repository._commit_reviewed`
now back public `Repository.execute(commit)` only. They copy/validate under the
real lock, preserve native hooks, publish stable index edits even on normal hook
rejection, and retain artifacts on unproven outcome. The UI and coordinator gate
have NOT been removed. Templates and multiline cleanup are separate pending work.

Public Repository writer test: red **1 failed in 0.70 s** → green **1 passed in
0.89 s**. A subsequent test exposed that checking only symbolic HEAD + direct
parent is insufficient: a post-commit `update-ref` can substitute another sibling
commit with that same parent. It incorrectly reports success: **1 failed in 1.51 s**.
The completed `commit-result-identity-review.md` informed a fix: bind Git’s full
native-created OID from its constrained success header and verify final HEAD,
symbolic target and raw parents. `commit-native-sources.md` corrects the original
hook-stdout assumption using pinned source. The sibling case is now green in
the combined component run.

HEAD/ref movement and post-commit index publication/durability are separate failure
boundaries. Split/sparse/linked/unborn indexes, hook/signing success and rejection,
ref races, interruption retention, and publication failures still need real Git
proof before either coordinator or UI gate can be removed. Representation and
synthetic-signing protocol coverage now exists; latest current-host component run
was **44 passed in 35.24 s** (`task-18`, including patches). Forced split-index
base replacement also exposed unsafe native sidecar expiration; per-call expiry
deferral now preserves the still-visible real index. Interruption/publication
fault proofs, coordinator integration and composer semantics remain pending.
