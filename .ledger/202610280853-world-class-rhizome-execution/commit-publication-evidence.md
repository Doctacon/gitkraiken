# Native commit publication fault evidence

Date: 2026-10-28

## Scope

Owned changes are limited to `tests/test_commit_publication.py` and this commit-publication evidence/result. The proof enters through public `Repository.execute(Action("commit", ...))` against a disposable real Git repository. Native Git creates all index fixtures; fault injection is confined to the `subprocess.Popen.wait`/filesystem boundary after native Git has returned. No private `Repository` or coordinator mock, product edit, gate bypass, user-root operation, network/provider operation, installation, publication, or project commit was used.

## Candidate-directory ownership substitution proof

Added `test_candidate_directory_substitution_after_native_git_is_not_published` as the first requested case.

The fixture:

1. Creates a base commit, stages reviewed content, and records the real index's bytes, inode identity, and staged entries.
2. Uses native Git with `GIT_INDEX_FILE` to create a complete, valid foreign index containing a distinct `foreign-only.txt` entry, plus a sentinel foreign `context.txt`. It records both files' bytes/content and inode identities.
3. Observes the real native commit only at `subprocess.Popen`. Its wrapped `wait()` first waits for native Git to settle, then records the owned transaction directory/index/context and real-lock identities.
4. At that boundary, moves the entire Rhizome-owned transaction directory aside and renames the distinct complete foreign directory into the transaction's former pathname.
5. Requires the public operation to fail uncertain without appending to foreign context, publishing/moving/deleting the foreign index, changing the byte-identical real index, deleting the owned lock, or altering the moved-aside original transaction data.

The ownership substitution is deliberate fault injection at a settled native-process boundary. The proof does not claim atomic protection against arbitrary same-UID races; it requires fail-closed handling once pathname ownership no longer matches the transaction's captured directory identity.

## First genuine red

Command:

```text
uv run --locked pytest -q tests/test_commit_publication.py::test_candidate_directory_substitution_after_native_git_is_not_published
```

Result:

```text
1 failed in 1.01s
AssertionError at tests/test_commit_publication.py:122
assert (transaction_path / "context.txt").read_bytes() == foreign_context_before
```

Passing preconditions before the failure establish that:

- the foreign index was valid, complete, semantically distinct from the real index, and contained `foreign-only.txt`;
- native Git returned `0` before substitution;
- `Repository.execute` surfaced `GitOutcomeUncertain` rather than a successful result;
- `HEAD` advanced to a commit containing the reviewed `example.txt` content;
- the directory at the original transaction pathname still had the captured foreign directory identity.

The first post-operation safety oracle then failed: Rhizome appended observations to the foreign `context.txt` before recognizing that the owned candidate directory had been replaced. This is the requested genuine ownership-substitution red.

## Stop point

Stopped immediately at this first red. Assertions for foreign-index retention, moved-aside original data, real-index bytes/identity, and owned-lock retention remain encoded after the failed context oracle but did not execute in this run. Candidate-fsync, first/final rename, post-replacement directory-fsync, real-lock ownership, linked-worktree, and split-index publication failures were not added or run. Ruff, the existing fault suite, and broader tests were not run after the red. No product code was changed and no assertion was weakened.
