# Fetch scope integration — root evidence

2026-10-28. This supersedes the temporary fetch-gate status, not any commit gate or milestone gate. All repositories/remotes are disposable and local. No real provider mutation, installation, publication or project commit occurred.

## Root review and failures

- An unreadable remote-ref directory was silently skipped by `os.walk`; a dangling cache symref could then create an out-of-scope local branch. Public `Repository.execute` regression failed to refuse.
- Replaced the worker test's private validation call with public `OperationCoordinator.plan`. Both global/per-remote tag-prune cases failed to refuse at planning. These three behavioral reds: **3 failed in 2.04 s**.
- `os.walk` now raises on inventory errors. Planning evaluates `_validate_fetch_scope` and binds its serialized scopes alongside effective configuration. Initial complete slice: **24 passed in 13.15 s**, Ruff green.
- Explicit `remote.origin.tagOpt=--no-tags` was unnecessarily refused. Red **1 failed in 0.63 s**; scope now captures and pins `--no-tags`, retaining default non-force auto-follow. Slice: **25 passed in 13.57 s**.
- Actual Git 2.36 exposed unsupported `symbolic-ref --no-recurse` on a normal cloned remote HEAD. Removed that flag; immediate loose targets remain recorded and recursive terminal resolution uses supported Git. Engine regression plus fetch suite: **27 passed in 9.86 s**.

## Inbound symbolic refs

The read-only consultant report `fetch-symbolic-alias-review.md` was static judgment, not executed evidence. Root supplied the real proof:

- A private commit reachable only through a cache plus a local branch, tag or notes alias became **unreachable** after fetch. Reflogs were disabled; `git fsck --unreachable --no-reflogs` proved loss of reachability, rather than merely asserting a hypothetical risk.
- Public preservation regression: **3 failed in 2.34 s**.
- Loose storage inspection now covers user-ref directories too, so unreadable inbound storage cannot disappear silently. Native `for-each-ref` supplies resolving user aliases. Validation rejects their intersection with direct configured cache destinations or terminal targets of symbolic cache destinations. No universal graph framework or recovery bypass was added.
- Fixed suite: **28 passed in 15.59 s**.
- Added native-twin tag oracles, dangling inbound alias creation, and indirect cache-destination coverage. Existing nonzero tag/branch targets remain unchanged. Previously dangling aliases may become resolvable without losing an old tip, including native tag auto-follow through a dangling symbolic tag.
- Both versions passed the unchanged 33-case matrix: current Git **33 passed in 19.49 s**; actual Git 2.36 **33 passed in 8.86 s**; Ruff green.

## Coordinator and UI restoration

- Added public coordinator success for selected/all eligible remotes and revalidation of newly unsafe destinations/inbound aliases before transport. All four were red against the temporary coordinator gate: **4 failed, 33 deselected in 3.20 s**.
- Removed only the fetch availability gate, leaving commit/destructive gates intact. Complete fetch suite: current Git **37 passed in 23.42 s**; actual Git 2.36 **37 passed in 10.94 s**; Ruff green.
- Added a manual Pilot fetch with automatic remote refresh explicitly disabled through `WorkspaceStateStore`. It proves the cache is old before the click, advances afterward, and HEAD/index bytes/unstaged edits remain unchanged. Red **1 failed in 2.08 s** against the disabled Fetch control.
- Restored the visible Fetch label/control (disabled while busy/offline/without a remote). Automatic fetch already uses the reviewed coordinator; no direct-mutation bypass was introduced.
- `task-25`: `test_fetch_workspace.py`, `test_refresh.py`, `test_refresh_workspace.py`, `test_sidebar_integration.py`, `test_operations_workspace.py`: **29 passed in 97.37 s**, Ruff green. This includes real stalled-transport local-inspection and automatic-fetch/serialization/visibility regressions.
- README, usage and Help now describe scoped fetch rather than a temporary global fetch gate. Consequences disclose FETCH_HEAD/cache updates, native tag auto-follow and configured cache pruning.

## Limits

This is local current/minimum-Git behavior on one host plus headless Pilot evidence, not actual provider/terminal/platform/release acceptance. Unsupported explicit tag modes/ref storage and unsafe mappings fail closed. Validation does not claim atomicity against arbitrary same-UID ref/config writes after validation, or sandbox trusted executable Git configuration. No changed full suite has passed. Ordinary commit remains unavailable pending foreground-descendant and publication proofs.
