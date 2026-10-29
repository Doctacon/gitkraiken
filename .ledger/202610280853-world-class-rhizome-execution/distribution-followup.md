## Distribution safety follow-up

Date: 2026-10-28

Scope was limited to the installer/distribution CLI, focused tests, two install constraint manifests, and this note. No publication or real-home install was performed.

### Implemented controls

1. **Dependency and interpreter provenance**
   - `scripts/install-runtime-constraints.txt` is the production dependency closure exported from `uv.lock`; each universal wheel is selected by its official `files.pythonhosted.org` URL and SHA-256 fragment.
   - `scripts/install-build-constraints.txt` pins hatchling 1.33.0 and its complete Python 3.13 build closure: packaging 26.3, pathspec 1.1.1, pluggy 1.7.0, tomlkit 0.15.2, and trove-classifiers 2026.9.21.13. Each is an official wheel URL with the supplied PyPI digest. The Python-<3.11-only tomli edge is intentionally absent.
   - The installer passes both files to uv 0.11.7 with `--constraints` and `--build-constraints`, fixes the default index to HTTPS PyPI, disables alternate project sources/keyring, and pins uv-managed CPython 3.13.13. Rhizome remains commit-pinned; the application floor was not changed.
   - An isolated offline uv 0.11.7 parser probe accepted the direct-URL/hash constraint syntax and reached retrieval of the exact constrained Textual wheel; it then failed as expected because networking was disabled and the temporary cache was empty. This was syntax/source-selection evidence, not an installation.

2. **Ambient configuration and public authentication**
   - Installation rejects every inherited `UV_*` variable before downloads or uv execution, covering named and dynamic index credentials as well as mirrors, custom Python metadata, constraints, insecure hosts, and auth providers.
   - Curl uses `-q --proto '=https' --tlsv1.2`; wget uses `--no-config --https-only --secure-protocol=TLSv1_2`.
   - uv runs with a private HOME/config directory, no system/global Git config, disabled credential helper/keyring, disabled terminal/askpass prompts, and cleared repository/SSH/proxy-command/extra-header Git variables. Standard proxy and CA environment variables are deliberately preserved.
   - The update-check command now prints the config-independent HTTPS-only curl form.

3. **State and deletion safety**
   - Install and state paths must be absolute and normalized. Both are resolved through their nearest existing physical ancestor, and state must be provably outside the physical install root.
   - Uninstall repeats physical path, ownership, directory-shape, and top-level allowlist validation immediately before removing the canonical physical root.
   - Unexpected top-level content fails closed rather than being deleted.

4. **Interruption safety**
   - A new root is built as a marked sibling skeleton and atomically renamed into place.
   - Runtime payloads live at their final physical `store/<commit>` path before uv runs, avoiding invalid absolute venv/shebang paths. A positively marked temporary skeleton is atomically moved there before population.
   - A completed payload is published through a separate positively marked `revisions/.staging-<commit>` record. Empty or correctly marked interrupted skeletons are recoverable; foreign content fails closed. A completed payload with interrupted publication is reused without rerunning uv.
   - An ownership-marked install lock prevents concurrent cleanup and permits dead-owner retry.
   - Rollback writes a single atomic journal before changing either pointer; retry completes the journal idempotently.

5. **Runtime isolation and output correctness**
   - The Rhizome launcher exports no installer `UV_*` settings, so application children and hooks do not inherit private uv paths from the installer.
   - A separate stable `bin/uv` wrapper scopes private uv directories to explicit private-uv invocations. Installer output prints this real wrapper path rather than treating the regular `current` pointer file as a directory.

6. **Test corrections**
   - Every installer subprocess fixture now sets `RHIZOME_STATE_HOME` explicitly and removes harness-created ambient `UV_*` variables.
   - Checksum rejection remains a checksum test. Real post-staging failure evidence uses TERM/KILL during uv execution.
   - Additional SIGKILL injection targets both the marked payload-staging move and completed-payload publication move; retries recover each exact state. A synthetic partial rollback journal verifies pointer recovery.
   - Tar, checksum, downloader, uv, managed Python, and installed application remain explicit boundary doubles in these tests.

### Validation evidence

Progression while implementing:

- First updated installer run: `uv run --locked pytest -q tests/test_install.py` — 3 passed, 13 failed because `uv run` injected `UV_RUN_RECURSION_DEPTH`; this exposed and led to explicit subprocess-environment isolation.
- Next run — 15 passed, 1 failed because the test expected uv paths under the renamed staging directory; this led to the non-relocated `store/<commit>` payload layout.
- After stable payload layout — 16 passed.
- After exact payload/publication interruption injection and recovery — 18 passed.
- Final: `uv run --locked pytest -q tests/test_install.py tests/test_cli.py` — 27 passed in 14.98s.
- Final: `uv run --locked ruff check src/rhizome/distribution.py src/rhizome/cli.py tests/test_install.py tests/test_cli.py` — passed.
- Final: `sh -n scripts/install.sh` — passed.
- Final: `git diff --check -- scripts/install.sh scripts/install-runtime-constraints.txt scripts/install-build-constraints.txt src/rhizome/distribution.py src/rhizome/cli.py tests/test_install.py tests/test_cli.py .ledger/202610280853-world-class-rhizome-execution/distribution-followup.md` — passed.

### Evidence limits

No real uv archive, managed Python download, GitHub clone, PyPI wheel download, TLS/proxy path, relocation behavior, or console entry point was executed. The test doubles establish command construction, environment boundaries, state transitions, cleanup, and launcher behavior only. Lightweight-tag and live GitHub update checks also remain unexercised. No private network/provider call, publication, release, or write outside isolated test/temporary paths was made.
