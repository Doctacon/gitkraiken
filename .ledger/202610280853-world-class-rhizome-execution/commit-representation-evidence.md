# Native commit representation evidence

Date: 2026-10-28

## Scope

Added `tests/test_commit_representations.py`. Every Rhizome commit transaction under test enters through the public `Repository.execute(Action("commit", message=...))` interface against disposable real Git repositories; direct Git creates fixture history. The native-twin sparse comparison uses direct Git only as its behavioral oracle. No coordinator/UI path, internal Repository mock, network/provider operation, skip, or bypass is used.

## Covered behavior

- Unborn initial commit: the reported 40-hex OID is the resulting `HEAD`; raw commit headers have the captured staged tree and no parent; committed/index content is the reviewed staged blob while a later worktree edit remains unstaged.
- Detached `HEAD`: the reported OID is the resulting detached `HEAD`; raw headers contain exactly the prior detached OID as parent and the staged tree; the named branch stays at its old OID; committed/index/worktree sources remain distinct as expected.
- SHA-256 repository: local `git init --object-format=sha256` succeeds; Git reports `sha256`; the transaction reports and verifies a full 64-hex created OID with the exact 64-hex raw parent and preserves staged content.
- Linked worktree: the linked and primary administrative index paths are distinct; committing the linked index advances only the linked branch and leaves the primary branch, byte-for-byte primary index, staged entries, and staged content unchanged.
- Sparse index: a native-Git twin and the public transaction retain identical `ls-files --sparse --stage` and representation-preserving `ls-files --sparse -t` results. The public index retains the collapsed `040000 ... outside/` entry, its `S outside/` skip-worktree state, and two physical index entries, matching native Git. Full tree OIDs match; outside blobs/content remain committed and absent from the sparse worktree.
- Split index durability and expiry stress: with `core.splitIndex=true` and `splitIndex.sharedIndexExpire=now`, a real `post-commit` hook blocks on a test-owned release file. While native commit is paused, the real index remains byte-identical, its original shared sidecar still exists, and real-index `ls-files --stage` returns all original entries. The `finally` path always creates the release file and joins the Git-owning thread; Git is never killed. After publication, the real index is readable, has an existing durable shared sidecar, and no transaction temporary directory remains.

## Exact validation results

Each behavior was added and run individually:

- split-index expiry stress: `1 passed in 1.49s`
- split-index stress after durable-publication assertions: `1 passed in 1.20s`
- unborn initial commit: `1 passed in 0.78s`
- detached HEAD: `1 passed in 0.85s`
- SHA-256: `1 passed in 0.79s`
- linked worktree isolation: `1 passed in 0.93s`
- sparse native-twin representation: `1 passed in 1.25s`

Final assigned-file validation:

- `uv run --locked pytest -q tests/test_commit_representations.py` — `6 passed in 6.08s`
- `uv run --locked ruff check tests/test_commit_representations.py` — `All checks passed!`

## Limits

- This is focused representation/transaction evidence only; the full test suite was not run.
- Coordinator and UI commit submission remain gated and were not exercised or enabled.
- SHA-256 evidence is for the current host Git only; no unsupported-host result is hidden by a skip.
- No network, provider, installer, publication, or user-root operation was performed.
- No commit, milestone, or approval claim is made.
