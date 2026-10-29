## Real blockers

1. **The executable chain is not reproducible beyond the Rhizome commit.**  
   `scripts/install.sh:340-342` pins Rhizome’s Git SHA, but `uv tool install` receives no lock-derived constraints or hashes. `pyproject.toml:7` permits any `textual>=1.0,<2.0`, and `pyproject.toml:12` leaves the build backend unpinned. Two installs of the same commit can therefore resolve different executable environments.  
   **Remedy:** bind runtime and build dependencies to immutable, release-owned lock-derived input, including hashes where supported. This needs no publication.

2. **Ambient configuration can redirect downloads, resolution, or credentials.**  
   `scripts/install.sh:291-294` invokes curl/wget without disabling user config. `scripts/install.sh:327-342` sets selected uv variables and `UV_NO_CONFIG`, but inherits other `UV_*` variables that can supply indexes, constraints, mirrors, custom Python metadata, insecure hosts, or credential providers. The printed curl command has the same issue at `src/rhizome/distribution.py:119-128`. Checksum verification protects the uv bytes, but not credential leakage; inherited uv settings can alter and execute later Python/package inputs.  
   **Remedy:** use config-independent, HTTPS-only curl/wget invocation; reject or clear source/security/auth-affecting `UV_*` variables before uv runs; disable interactive Git/keyring authentication for these public fetches while deliberately preserving documented proxy/CA behavior.

3. **The state-preservation check can be bypassed by a relative state path.**  
   `scripts/install.sh:123-134` canonicalizes state only when the path currently names a directory; otherwise it compares the raw value. The application accepts a relative `RHIZOME_STATE_HOME` and resolves it against its launch directory (`src/rhizome/persistence.py:58-70,182-184`). An app launched from inside the install root with `RHIZOME_STATE_HOME=state` can store drafts under that root; uninstall from another directory will miss them and execute `rm -rf "$ROOT"` at `scripts/install.sh:194-197`.  
   **Remedy:** refuse destructive actions when state paths are relative or cannot be canonically proven outside the physical install root, and revalidate immediately before deletion.

4. **Abrupt interruption has unrecoverable publication gaps.**  
   A kill between root creation and its marker (`scripts/install.sh:312-313`) leaves an unmarked root that future runs refuse. A kill between revision-directory creation and its marker (`scripts/install.sh:318-320`) leaves an unmarked destination that fails the recovery logic at `scripts/install.sh:262-276`. Existing `current` remains runnable, so this is not prior-version loss, but retry/recovery is broken. Rollback also swaps two files separately (`scripts/install.sh:207-208`); interruption can leave both pointers equal and make retry fail.  
   **Remedy:** stage revisions under positively marked temporary names before atomic publication, safely recognize only exact empty bootstrap skeletons, and make rollback state a single atomic record or journaled operation.

## Material concerns

5. **Uninstall ownership is directory-wide, not file-specific.**  
   `require_owned_root` checks one root marker and two directory shapes (`scripts/install.sh:138-152`), then uninstall removes the entire tree (`scripts/install.sh:194-197`). Any foreign file subsequently placed in the marked root is deleted. This does protect unmarked pre-existing roots, but it is broader than preserving foreign content.  
   **Remedy:** fail closed on unexpected top-level entries before uninstall, and use one canonical physical root for validation and deletion to avoid symlink-ancestor retargeting.

6. **The runtime launcher leaks installer-specific uv configuration, and the printed private-uv path is invalid.**  
   `scripts/install.sh:383-388` exports `UV_TOOL_DIR`, `UV_CACHE_DIR`, `UV_NO_CONFIG`, and related variables into Rhizome and every Git hook/editor/tool child. A child invoking uv can silently use or modify Rhizome’s private revision. Separately, `scripts/install.sh:185` prints `$ROOT/current/uv/bin/uv`, but `current` is a regular pointer file written at `scripts/install.sh:174-179,410`, not a directory.  
   **Remedy:** keep uv variables in a dedicated private-uv wrapper rather than the application launcher, and provide a safe stable uv wrapper or print the resolved revision path.

7. **The “interrupted update” and state-preservation tests do not establish their claims.**  
   `tests/test_install.py:164-168` labels a checksum rejection before revision creation as “interrupted”; it does not exercise failure after `.rhizome-installing` or a signal. Also, `tests/test_install.py:99-103` inherits `RHIZOME_STATE_HOME` from `tests/conftest.py:14-18`, so the draft asserted at `tests/test_install.py:196` is not the state path the installer evaluated. The tar, checksum, uv, and application are explicit doubles (`tests/test_install.py:28,54-88`), and platform cases intentionally stop at checksum failure (`tests/test_install.py:211-221`).  
   **Remedy:** explicitly set the intended state override, inject post-marker uv failures/signals, and describe checksum rejection as such. Add local artifact/runtime integration separately from unit boundary tests.

## Unavailable evidence, not code defects

- The ledger correctly records that no real uv archive, managed Python, candidate commit, or platform install was exercised (`distribution.md:74-79`). Thus actual relocation, paths with spaces, archive layouts, proxy/TLS behavior, and console-entry-point execution remain unproven.
- Tag resolution is tested only with mocked annotated-tag responses (`tests/test_cli.py:56-86`). The full-SHA logic is present at `src/rhizome/distribution.py:64-89`, but lightweight-tag and real GitHub response evidence remains unavailable. This should remain an open gate; it does not require publishing anything now.