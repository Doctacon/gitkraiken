# Distribution hardening follow-up

Date: 2026-10-28

Scope stayed within `scripts/install.sh`, `src/rhizome/distribution.py`, `src/rhizome/persistence.py`, the three owned focused test files, and this note. `distribution.py` and `tests/test_cli.py` required no code change: their existing config-independent printed curl command remained correct. No live installation, publication, lockfile change, dependency change, or platform experiment was performed.

## Implemented

### Credential/config-independent acquisition and build

- Pinned uv remains 0.11.7, managed CPython remains 3.13.13, and the pinned runtime/build constraints remain in use.
- Downloader and private-uv subprocesses clear `NETRC`, `SSL_CLIENT_CERT`, `PYTHONPATH`, and `PYTHONHOME`.
- They use private `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME` locations.
- Repository, worktree, object, index, namespace, replacement, discovery, template, executable, config, SSH, extra-header, TLS-bypass/version/cipher, protocol, and trace Git overrides are cleared. The private uv Git configuration still disables system/global config, credential helpers, askpass, and terminal prompts.
- Standard proxy and CA settings remain intentionally available.
- wget now uses `--no-netrc` and `--no-hsts` in addition to its existing no-config/HTTPS/TLS controls. Download failures no longer interpolate the URL or ambient values.
- Curl and wget boundary-double logs verify that proxy/CA values remain while the named credential/config variables and ambient XDG locations do not reach downloads or uv. The wget fallback test runs with no curl on `PATH` and asserts `--no-netrc`, `--no-hsts`, no-config, HTTPS-only, and TLS arguments.

### One root-wide transaction lock

- Install, first-root publication, revision publication, stable-launcher replacement, pointer updates, rollback (including journal completion), and uninstall all run under one lock beside the installation root in its canonical physical parent.
- Paths, state separation, root ownership, and managed subtree shape are revalidated after lock acquisition.
- The lock records PID plus a private nonce. Dead-owner takeover requires an exclusive child `reaper` claim, owner reread, a second dead-owner check, and atomic movement of the complete old lock to a nonce-specific quarantine before a fresh outer lock can be claimed.
- Claimants rescan quarantines after claiming to close the scan/create race. They never delete a fresh outer lock based on an earlier stale observation.
- A fully recorded dead reaper/quarantine can be recovered. Missing or unverifiable owner/reaper records fail closed with an explicit manual-inspection message.
- Behavioral tests cover TERM/KILL install recovery, concurrent install/rollback/uninstall refusal while an update holds the lock, normal dead-owner recovery, and fail-closed interruption before a stale lock is moved.

### State exclusion and deletion ownership

- `WorkspaceStateStore` resolves physical ancestors and checks the installer ownership marker before initial directory creation, chmod, document fchmod, save, or clear.
- Actual-filesystem tests cover a state directory below `store/`, a symlink alias into a revision payload, and an already-open store whose ancestor later becomes marked. Refused construction creates no state path; refused later save/clear preserves existing bytes.
- Uninstall/root validation now rejects unknown direct children in `revisions`, `store`, `bin`, and `transactions`, while accepting only positively marked revision/payload staging states and known launcher/transaction names.
- The installer does not describe arbitrary nested files as known application state. Revision payload directories remain installer-owned payloads as a whole; see limits below.

### Private uv wrapper removal

- New installs no longer create or advertise `bin/uv`.
- A regular legacy `bin/uv` is removed only when the launcher directory has the installer ownership marker; symlinks or unmarked launchers are refused.
- The stable Rhizome launcher and pointer/rollback behavior are unchanged, and the Rhizome launcher exports no installer `UV_*` values.

## Exact red/green record

- First focused checks after the initial implementation:
  - `uv run --locked pytest -q tests/test_persistence.py` — **16 passed**.
  - `uv run --locked pytest -q tests/test_install.py tests/test_cli.py` — **29 passed**.
  - owned Ruff invocation — **passed**.
  - `sh -n scripts/install.sh` — **passed**.
- After adding mutation-time managed-ancestor checks and the concurrent-lock test:
  - combined focused pytest — **8 failed, 39 passed**. The failures showed that checking `file/.rhizome-install-root` on an existing document produced `ENOTDIR`; ancestor inspection was corrected to begin at the parent when the resolved path is a file.
- Focused persistence rerun after that correction — **17 passed**.
- The added wget-only boundary test initially reported **1 failed, 22 deselected** because its assertion spelled wget's existing `TLSv1_2` argument as `TLSv1.2`; correcting the assertion produced the expected behavioral coverage without changing the installer command.
- Final validation:
  - `uv run --locked pytest -q tests/test_install.py tests/test_cli.py tests/test_persistence.py` — **48 passed in 21.00s**.
  - `uv run --locked ruff check src/rhizome/distribution.py src/rhizome/persistence.py tests/test_install.py tests/test_cli.py tests/test_persistence.py` — **passed**.
  - `sh -n scripts/install.sh` — **passed**.
  - per-file `git diff --no-index --check /dev/null FILE` whitespace checks for every owned path (including currently untracked files) — **passed**.

## Remaining evidence and interruption limits

- No real uv archive, managed-Python artifact, GitHub checkout, PyPI artifact, TLS/proxy route, real wget network transfer, console entry point, live installation, publication, or target-platform behavior was exercised. Tests use external process boundary doubles and establish command/environment construction and filesystem state transitions only.
- A process killed after creating a fresh outer lock but before completely recording its owner, or after creating a reaper directory but before completely recording the reaper owner, leaves deliberately unverifiable state. Recovery fails closed and requires manual inspection; recursively stealing that state would recreate the unsafe check/delete race.
- Fully recorded TERM/KILL owners and fully recorded dead quarantine reapers are recoverable. PID reuse is treated conservatively as a live owner rather than guessed stale.
- Direct foreign children of managed subtrees are detectable and block deletion. Arbitrary files manually inserted deep inside a completed dependency payload cannot be distinguished from installed payload files without a new signed/complete payload manifest; no such format or dependency was introduced. Application state is independently blocked from every physically managed subtree.
- Physical-ancestor checks precede each owned write boundary, but they are not a substitute for an OS-specific directory-descriptor/openat transaction against a concurrently hostile filesystem. No new runtime dependency or platform-specific API was added.
