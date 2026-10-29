## Decision

Do not accept V05. The hardening closes several prior defects, but material interruption and ownership gaps remain. I do not authorize a live user-owned install or publication.

## Actual blockers

1. **Interrupted rollback state is not fenced from a later install.**  
   Only `--rollback` notices and completes `transactions/rollback` (`scripts/install.sh:498-531`). A subsequent `--revision` accepts that journal as ordinary transaction content (`scripts/install.sh:386-395`) and proceeds, leaving the old journal behind. A later `--rollback` then restores the stale pre-update pointers, not the newly requested rollback.  
   The test at `tests/test_install.py:644-661` only retries with `--rollback`; it does not attempt install over a pending journal.

2. **SIGKILL stale-lock recovery does not establish subprocess quiescence.**  
   Lock recovery checks only the recorded installer shell PID (`scripts/install.sh:227-245, 285-322`). If that shell is killed while foreground `uv`, Git, a build backend, or `rm -rf` survives, another invocation can reap the lock and delete/repopulate the same payload concurrently. The KILL double explicitly kills the parent and then exits immediately (`tests/test_install.py:109-111`), so it cannot expose this race.  
   This is not the excluded “arbitrarily daemonized child” case: an ordinary foreground child can outlive a directly killed shell.

3. **Uninstall still accepts foreign content under reserved pointer names.**  
   `current` and `previous` are allowlisted at `scripts/install.sh:326-334`, but `require_owned_root` never verifies that they are regular, non-symlink pointer files (`scripts/install.sh:397-418`). A directory named `current` or `previous` is therefore recursively deleted at `scripts/install.sh:482-493`. The managed subdirectories receive type checks; these two entries do not.

4. **Interrupted uninstall has no reproducible recovery transaction.**  
   Uninstall directly executes `rm -rf "$ROOT"` (`scripts/install.sh:493`). Disk/permission failure or interruption can leave a root missing its marker or required subdirectories, after which `require_owned_root` refuses both retry and cleanup (`scripts/install.sh:397-418`). No focused test interrupts uninstall. This does fail closed, but it does not meet the documented reproducible-recovery gate.

5. **Credential/config isolation remains blacklist-based and incomplete.**  
   The named prior leaks—`NETRC`, client certificates, Python path/home, Git selectors/config/askpass, ambient XDG homes, user curl/wget config, and `UV_*`—are addressed at `scripts/install.sh:540-541, 628-654, 749-778`. However, Git tracing controls such as `GIT_CURL_VERBOSE`, `GIT_TRACE_REDACT`, and `GIT_TRACE2*` remain inherited. With deliberately retained credential-bearing proxy variables, ambient tracing/redaction settings can expose transport details or write to an unrelated trace sink. The tests cover selected legacy trace variables, not these families (`tests/test_install.py:433-487`). An allowlisted subprocess environment would be simpler and safer.

## What is materially closed

- **Cooperating installer transactions:** install, first-root publication, rollback, and uninstall acquire the same physical-parent lock before root mutation (`scripts/install.sh:259-324, 477-480`). The prior fresh-lock deletion/ABA race is structurally fixed by exclusive reaper claims and nonce-specific quarantine.
- **State inside managed installation:** installer paths must be absolute and physically separate (`scripts/install.sh:137-193, 479-490`), while runtime state checks every resolved ancestor before creation and writes (`src/rhizome/persistence.py:89-142, 223-267, 321-378`). Symlink aliases and later marker appearance are covered by `tests/test_persistence.py:180-222`. This is prospectively closed for current code.
- **Public uv mutation:** there is no stable `bin/uv`; a legacy regular wrapper is removed only under the launcher ownership marker (`scripts/install.sh:818-836`). The private uv remains implementation payload, not a public mutation interface.
- **Direct foreign subtree entries:** unknown direct children are rejected (`scripts/install.sh:326-395`). Arbitrary files deep inside a completed dependency payload remain indistinguishable and are reasonably within the documented dedicated-root ownership boundary.

## Remaining concerns and boundary distinctions

- A narrow completion-marker edge remains at `scripts/install.sh:703-716, 793-815`: SIGKILL between writing `.rhizome-revision` and removing `.rhizome-installing` leaves both markers. Retry treats the payload as complete but does not remove the stale installing marker; a second trapped interruption immediately after revision publication can let cleanup delete the completed payload while leaving its publication record.
- First-root SIGKILL before publication can leave `.rhizome-root.$$.staging` beside the root (`scripts/install.sh:681-688`); future runs do not scavenge it.
- Legitimate concurrent `WorkspaceStateStore` writers remain last-writer-wins: validation/read and `os.replace` have no interprocess lock or compare-and-swap (`src/rhizome/persistence.py:248-267, 363-378`). This is material if multiple Rhizome processes may open the same workspace. Current application workspace keys are lowercase SHA-256 values (`src/rhizome/app.py:392-398`), so case-colliding keys are not a current app problem, although the public store accepts case variants (`src/rhizome/persistence.py:308-317`).
- Concurrent hostile same-UID path replacement, marker forgery, bind-mount aliases, and deliberately daemonized build processes remain outside the documented descriptor/openat boundary (`distribution-hardening.md:64`). I do not treat those alone as blockers.

## Evidence limits

I performed static read-only review and did not independently rerun tests or filesystem probes. I accept only the supplied report of 48 focused tests, Ruff, and `sh -n`. Those tests replace downloader, archive, checksum, uv, Python, GitHub checkout, and installed application boundaries. They do not establish real archive/Python/package provenance, proxy/TLS behavior, APFS case behavior, live process-tree interruption, interrupted uninstall, lightweight-tag API behavior, or a working first publication.

The advertised “Direct uv alternative” is also only prose at `scripts/install.sh:895-897`; V05 explicitly requires an exercised direct-uv path (`docs/design/validation-plan.md:285-302`). No candidate release or publication exists.