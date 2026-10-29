# Fetch-scope safety evidence

## Owned change

- `src/rhizome/git.py`: fetch-only validation/planning helpers and the `execute(Action("fetch"))` branch.
- `tests/test_fetch_safety.py`: sequential disposable real-Git tests through `Repository.execute(Action("fetch", remote=...))`.
- This evidence file.

Coordinator/UI fetch gates were not changed. No operation, application, native commit/index transaction, or other test/doc code was edited for this repair.

## Policy implemented

`Repository._validate_fetch_scope(requested_remote="")` is the private coordinator-planning seam. It returns the exact validated per-remote scopes and is also called by fetch execution. It is a fail-closed preflight, not a claim of atomicity against arbitrary concurrent same-UID ref/config writes.

The validator:

- reads effective included `remote.<name>.fetch`, global/per-remote `pruneTags`, tag mode, and fetch-all skip configuration;
- rejects malformed/unsupported and ambiguous positive mappings;
- permits positive destinations only below `refs/remotes/` while preserving source-only and negative refspecs supported by native Git;
- rejects effective tag pruning, including inherited configuration, with per-remote override semantics;
- rejects non-default remote tag modes so only native non-force tag auto-follow remains;
- recursively checks exact and wildcard-covered symbolic destinations, including dangling loose symbolic refs, and rejects resolution outside `refs/remotes/`;
- preserves both `skipDefaultUpdate` and `skipFetchAll` behavior for all-remote fetches on the tested Git;
- rejects unsupported ref storage rather than assuming dangling symbolic refs can be enumerated safely.

Execution performs one native fetch for each selected remote and no discovery transport. It passes the validated refspecs explicitly with an empty `--refmap=`, `--no-force`, and `--no-prune-tags`, and pins global/per-remote `pruneTags=false`. A remote with no configured refspec is pinned to source-only `HEAD`, preserving native `FETCH_HEAD` behavior without allowing a later configured destination to broaden the command. Remote-cache force markers and configured cache pruning remain effective.

## Red-to-green evidence

Host used for all evidence:

- Darwin 25.6.0 arm64
- Git 2.50.1 (Apple Git-155)
- Python 3.13.0

Initial red run after adding the bounded tests and before implementation:

```text
$ uv run --locked pytest -q tests/test_fetch_safety.py
10 failed, 6 passed in 8.72s
```

Failures covered branch/tag/other-user destinations, global/per-remote tag pruning, inherited configuration, recursive/dangling/wildcard symbolic escapes, and ambiguous mappings. The six native passes established the pre-repair compatibility baseline for safe cache operations, negative/source-only behavior, tag auto-follow, and fetch-all skips.

Final owned suite:

```text
$ uv run --locked pytest -q tests/test_fetch_safety.py
23 passed in 14.18s
```

The real disposable repositories prove:

- rejection before fetch of `refs/heads/*`, `refs/tags/*`, and `refs/notes/*` destinations, including preservation of the checked-out branch and `HEAD` when the remote has a different commit;
- rejection of global and per-remote effective tag pruning, inherited unsafe refspecs, and inherited tag pruning;
- per-remote `pruneTags=false` overrides global true while remote-cache pruning still removes a stale tracking ref and preserves a private local tag;
- rejection of recursive, dangling, and wildcard-covered symbolic destinations that resolve to branch/tag namespaces;
- preservation of negative exclusions and source-only `FETCH_HEAD` semantics;
- default tag auto-follow creates an absent reachable tag but preserves a conflicting existing local tag;
- no-config refspec fetch is pinned to remote `HEAD` without creating a tracking ref;
- both native fetch-all skip keys avoid the skipped remote;
- malformed/unsupported, ambiguous cache mappings, and non-default tag mode fail closed.

Lint for the owned implementation/tests:

```text
$ uv run --locked ruff check src/rhizome/git.py tests/test_fetch_safety.py
All checks passed!
```

A separate disposable real-Git probe on the same host confirmed `git fetch --all` skips a remote for either `remote.<name>.skipDefaultUpdate=true` or `remote.<name>.skipFetchAll=true`.

## Limits

- Tests used local disposable file remotes only; no live network, provider mutation, installer, publication, or commit was performed.
- No full suite, UI test, coordinator integration, human pilot, M1 readiness, or other-platform result is claimed.
- This does not sandbox Git helpers/hooks/configured executables and does not provide arbitrary cross-process config/ref atomicity.
- A multi-remote fetch can still update objects, `FETCH_HEAD`, or validated remote-cache refs before a later remote fails.
