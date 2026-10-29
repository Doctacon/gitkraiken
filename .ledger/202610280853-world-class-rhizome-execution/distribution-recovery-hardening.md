# Distribution recovery hardening

Date: 2026-10-28

Scope was limited to `scripts/install.sh`, `tests/test_install.py`, and this note. No live/user-root installation, network download, dependency change, publication, commit, source change, documentation change, or other test-file change was performed. M1 and V05 remain unaccepted.

## Implemented

- Pending rollback journals are reconciled under the root transaction lock before a later install or uninstall can proceed. Journal structure and both owned targets are validated before pointer completion.
- Foreground acquisition/build/deletion work is fenced with a lock activity record created before process launch and removed by the surviving wrapper only after the foreground command returns normally (including an ordinary nonzero result). HUP/INT/TERM disable EXIT cleanup and retain the record because descendants may survive. A dead installer PID is no longer sufficient for lock takeover: any present or unverifiable activity record fails closed for manual inspection. The same guard covers curl/wget, checksum, tar extraction, private uv/Python/Git/build work, staged-payload cleanup, first-root remnant cleanup, and uninstall deletion.
- A real subprocess test keeps an ordinary foreground child alive after the installer shell is killed. A second invocation is refused while the child remains alive, and its work is unchanged. The test releases the child’s fixture hold and requires natural exit; it does not kill the child to claim recovery.
- `current` and `previous`, when present, must be regular non-symlink pointer files resolving to marked, usable owned revisions. Uninstall tests retain directory contents, symlink targets, and malformed regular files and prove refusal.
- Uninstall now validates the complete root, creates a sibling owned recovery transaction, atomically moves the root into it, and deletes only the quarantined `root` while the transaction’s separate owner record survives. Retry can therefore finish a partially deleted staged root without relying on an internal marker that `rm` may already have removed. A controlled external `rm` performs a partial deletion, the installer shell is killed, the child exits naturally, and retry completes the owned transaction.
- Acquisition and private-build subprocesses now start with `env -i`. The explicit environment contains private HOME/XDG paths, pinned uv/Git controls, inherited PATH, and an intentional proxy/CA allowlist (`HTTP_PROXY`/lowercase variants, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `CURL_CA_BUNDLE`, and `REQUESTS_CA_BUNDLE`). Ambient credential/config selectors and all unlisted variables, including `GIT_CURL_VERBOSE`, `GIT_TRACE_REDACT`, and every `GIT_TRACE2*` name, are absent. Fixture commands embed/derive test-private paths rather than requiring production `FAKE_*` passthrough at these boundaries.
- A payload carrying both valid completion and installing markers is reconciled by validating and removing the stale installing marker before publication. A subsequent forced publication interruption proves cleanup preserves the completed payload.
- Positively marked first-root staging remnants with only known empty children are scavenged under the activity fence. Unmarked or foreign remnants fail closed and are preserved.

## Behavioral red/green record

The first new focused run, before implementation, was:

- `uv run --locked pytest -q tests/test_install.py -k 'reconciles_an_interrupted_rollback_before_updating or foreign_reserved_pointer_directories or completed_payload_with_installing_marker or owned_first_root_staging_remnant'` — **5 failed, 23 deselected in 6.35s**. The failures demonstrated the stale journal, reserved-pointer deletion, dual-marker cleanup, and first-root remnant gaps.

After the first implementation pass, the same selection was **5 passed, 23 deselected in 5.82s**.

The fixture was then converted to test-private filesystem controls so the production allowlists did not gain test-only variables. One full focused run found only a missing `dirname` executable in the intentionally minimal wget fixture PATH (**1 failed, 29 passed in 35.56s**); adding that existing host command to the fixture boundary made the wget selection pass. Later activity-guard tightening exposed an expected test-policy mismatch and then an EXIT-trap cleanup defect; the test moved routine tool failure away from signal semantics, and the guard now explicitly records command status and clears activity on ordinary nonzero exit while retaining fail-closed records for unprovable interruption.

A final signal-path test then exposed that HUP/INT/TERM exited through the wrapper's EXIT cleanup. The traps were corrected to disable EXIT cleanup on those signals. The behavioral test proves refusal while the foreground child remains alive and, after its natural exit, proves the retained activity record still fences recovery.

## Final validation

- `uv run --locked pytest -q tests/test_install.py` — **36 passed in 39.53s**.
- `uv run --locked ruff check tests/test_install.py` — **passed** (`All checks passed!`).
- `sh -n scripts/install.sh` — **passed**.
- Per-file `git diff --no-index --check /dev/null FILE` for `scripts/install.sh`, `tests/test_install.py`, and this ledger note — **passed with no diagnostics**. Exit status 1 is the expected no-index “files differ” status for each untracked owned file, not a whitespace diagnostic.

## Policy choices and limits

- Lock recovery remains conservative. A dead owner with no activity record can be recovered; a live owner, any activity record, malformed record, incomplete reaper, or unknown process state requires manual inspection. This deliberately trades automatic recovery after an unprovable wrapper interruption for non-concurrent mutation.
- Inherited `PATH` is intentionally allowlisted because uv must locate Git and the bootstrap must locate platform tools. Proxy and CA variables are intentionally retained as listed above. No other ambient acquisition/build variable is passed.
- The uninstall transaction’s sibling owner record is the recovery authority after atomic quarantine. Internal staged-root shape is not revalidated after partial deletion because successful deletion may already have removed any internal marker or required directory. Concurrent hostile same-UID replacement remains outside the documented boundary.
- Tests use real disposable filesystem/process state and controlled external commands. They do not establish real uv/Python/package provenance, live TLS/proxy behavior, target-platform behavior, a user-root install, publication, or a working first release.
