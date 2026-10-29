I could not create the requested file because this agent is structurally read-only. Suggested contents:

```markdown
# Distribution follow-up safety review

## Decision

**Conditional go for one real disposable install experiment, not general installer or release approval.** Use a freshly constructed credential-free environment with temporary `HOME`, all XDG locations, `TMPDIR`, install root, and state root; preserve only explicitly authorized proxy/CA settings and run no concurrent install/rollback/uninstall. This avoids the remaining ambient and concurrency defects while obtaining the missing real uv/GitHub/Python evidence.

## Material findings

1. **Ambient credential isolation remains incomplete.**  
   `scripts/install.sh:340-341` rejects only `UV_*`. The uv subprocess replaces `HOME` and `XDG_CONFIG_HOME` but otherwise inherits the environment (`scripts/install.sh:541-557`). uv 0.11.7 explicitly consumes `NETRC`, `SSL_CLIENT_CERT`, and `PYTHONPATH` (`bootstrap-sources/uv-environment-rest.md:301-305,363-367,457-461`); the first two are authentication material and the last can affect isolated build imports. `XDG_DATA_HOME` also remains ambient, leaving uncertainty around uv’s plaintext credential storage. Git variables such as `GIT_TEMPLATE_DIR` and `GIT_SSL_NO_VERIFY` are not cleared. The wget fallback runs before private `HOME` and has neither `--no-netrc` nor `--no-hsts` (`scripts/install.sh:422-431`), so it can read credentials and update the caller’s HSTS database. The claim that installation uses no ambient credentials is therefore not established.

2. **Single-process interruption handling improved, but root mutation is not serialized.**  
   Root publication occurs before lock acquisition (`scripts/install.sh:466,475`); a concurrent first install can make `mv stage root` nest the stage inside an unexpectedly appearing directory. Stale-lock recovery is check-then-delete (`scripts/install.sh:476-488`), allowing two retriers to delete or steal each other’s newly acquired lock. More importantly, the lock is removed at `scripts/install.sh:597-598` before stable launchers and pointers are updated at `scripts/install.sh:601-711`; rollback and uninstall never participate in that lock. The rollback journal is sound for one interrupted process, but concurrent install/rollback/uninstall can still lose availability or publish inconsistent pointers.

3. **State preservation still depends on the uninstall invocation’s environment.**  
   Uninstall validates only the state path resolved at that invocation and only allowlists top-level root names before `rm -rf` (`scripts/install.sh:179-198,282-294`). The application independently accepts a relative or different `RHIZOME_STATE_HOME` and absolutizes it against launch CWD (`src/rhizome/persistence.py:57-70,182-184`). State previously written below an allowed subtree such as `store/` can therefore be invisible to a later default-path uninstall and be deleted. Likewise, foreign content below `store`, `revisions`, `bin`, or `transactions` is not detected; ownership remains whole-root rather than payload-specific.

4. **The advertised private uv wrapper unnecessarily exposes the live payload.**  
   `bin/uv` points `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` directly at the selected immutable-looking revision (`scripts/install.sh:658-694`). A user can install/uninstall tools, including Rhizome, inside the live payload; rollback then selects that mutated payload and uninstall deletes any added tools. The application launcher itself no longer leaks installer `UV_*`, which fixes the prior runtime-environment issue, but the mutable uv wrapper weakens the payload/rollback ownership model and adds little value.

## Pinning assessment

The static pinning is materially stronger:

- Runtime closure entries are exact `files.pythonhosted.org` wheel URLs with SHA-256 fragments (`scripts/install-runtime-constraints.txt:4-12`) and match `uv.lock`.
- Hatchling and its Python 3.13 build closure are similarly fixed (`scripts/install-build-constraints.txt:3-8`); saved PyPI metadata supports the listed closure (`bootstrap-sources/build-packages.json:7-14`).
- The command passes both `--constraints` and `--build-constraints`, fixes PyPI, disables project sources/keyring, and requires private managed Python 3.13.13 (`scripts/install.sh:567-573`).

That is credible command construction, not real proof. uv’s help says build constraints apply while building source distributions, but the recorded offline probe reached only the constrained Textual runtime wheel, and the tests replace downloader, archive, uv, Python, and application boundaries (`distribution-followup.md:13,41,56-58`; `tests/test_install.py:50-117`). A real isolated run still needs to demonstrate the exact runtime wheels, isolated Hatchling closure, managed-Python artifact, Git commit checkout, and working console entry point. Do not describe existing tests as that evidence.

The config-independent printed curl command is fixed (`src/rhizome/distribution.py:128-132`). Lightweight-tag/live update-check evidence remains separate and unresolved.

## Recommendation

Proceed only with the controlled disposable experiment described above. Treat ambient authentication, cross-action locking, latent nested state, and the mutable live uv wrapper as remaining product-safety work. This review does not approve V05/V06, a milestone, publication, or release.
```