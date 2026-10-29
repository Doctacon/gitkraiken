## Implementation note: inspectable POSIX bootstrap

**Scope.** C10 requires a small, release-pinned GitHub bootstrap, managed Python 3.13, explicit updates, rollback/uninstall, no elevation, and no silent edits to Git, tmux, or shell configuration (`docs/design/world-class-rhizome.md`, C10). V05 requires isolated-home testing (`docs/design/validation-plan.md`, V05). These local, uncommitted specifications have no verified published URL. The project source URL evidenced by README.md is `https://github.com/Doctacon/rhizome`, not an inferred account name.

Use a downloaded, inspected uv release artifact rather than piping an installer. The supplied uv documentation supports requesting a version in the installer URL, but does not document checksum verification or a custom installation directory (`bootstrap-sources/uv-installation.md`; official URL: `https://docs.astral.sh/uv/getting-started/installation/`). Therefore the bootstrap should use a temporary directory, verify the archive before extraction, and install only its selected binary into a user-owned directory. It must not invoke `uv update-shell`, shell completion commands, or any profile-editing installer behavior. Set the documented environment variable:

```sh
UV_NO_MODIFY_PATH=1
```

This disables profile modification during uv self-update (`bootstrap-sources/uv-installation.md`; official URL above).

For managed CPython, the documented argv is:

```text
uv python install 3.13
uv tool install --python 3.13 PACKAGE
```

`uv python install 3.13` installs a managed Python version; `uv tool install` creates an isolated persistent tool environment, and `--python` selects its Python version (`bootstrap-sources/uv-python.md`, `bootstrap-sources/uv-tools.md`; fetched official URLs: `https://docs.astral.sh/uv/concepts/python-versions/`, `https://docs.astral.sh/uv/concepts/tools/`). `uv python install` normally installs Python executables under `~/.local/bin`; the bootstrap should not add that directory to `PATH`, but invoke absolute paths or provide a printed, temporary command prefix.

The documented state categories are:

- uv binaries: `~/.local/bin/uv` and `~/.local/bin/uvx`;
- managed Python: uv’s Python directory, discoverable with `uv python dir`;
- installed tool environments: uv’s tools directory, discoverable with `uv tool dir`;
- disposable tool environments and downloads: uv’s cache directory (`uv cache clean` removes cached data).

The supplied documents do not establish every platform’s exact cache/tools-directory defaults, nor a documented environment-variable interface for relocating them. Those locations must therefore be reported by the bootstrap, not guessed (`bootstrap-sources/uv-installation.md`, `uv-python.md`, `uv-tools.md`; official URLs above).

The supplied sources do **not** document an exact `uv tool install` argv for a GitHub VCS URL pinned simultaneously to a commit and package version. Do not invent one. A commit-pinned VCS installation remains **unsupported/unverified** until tested against the relevant uv CLI documentation and an isolated environment. Likewise, no Rhizome release artifact URL, package name, executable name, or commit is present in the supplied sources; those values cannot be specified safely here.

For uv 0.11.7, select the archive matching OS/architecture and verify its complete SHA-256 digest before extraction:

- `uv-aarch64-apple-darwin.tar.gz`: `66e37d91f839e12481d7b932a1eccbfe732560f42c1cfb89faddfa2454534ba8`
- `uv-x86_64-apple-darwin.tar.gz`: `0a4bc8fcde4974ea3560be21772aeecab600a6f43fa6e58169f9fa7b3b71d302`
- `uv-aarch64-unknown-linux-gnu.tar.gz`: `f2ee1cde9aabb4c6e43bd3f341dadaf42189a54e001e521346dc31547310e284`
- `uv-x86_64-unknown-linux-gnu.tar.gz`: `6681d691eb7f9c00ac6a3af54252f7ab29ae72f0c8f95bdc7f9d1401c23ea868`
- `uv-aarch64-unknown-linux-musl.tar.gz`: `46647dc16cbb7d6700f762fdd7a67d220abe18570914732bc310adc91308d272`
- `uv-x86_64-unknown-linux-musl.tar.gz`: `64ddb5f1087649e3f75aa50d139aa4f36ddde728a5295a141e0fa9697bfb7b0f`

These URL, digest, and size records come from `bootstrap-sources/uv-release.json`, whose official API source is `https://api.github.com/repos/astral-sh/uv/releases/tags/0.11.7`.

Updates must be explicit: replace the selected pinned uv artifact only after verification; use `uv tool upgrade PACKAGE` only when changing a tool within its declared constraints. Rollback means restoring the previously retained uv binary and reinstalling the previously pinned tool specification. Uninstall means `uv cache clean`, removing the directories reported by `uv python dir` and `uv tool dir`, then removing only the owned uv/uvx binaries; the supplied uv documentation gives this sequence (`bootstrap-sources/uv-installation.md`; official URL above). No profile, `PATH`, Git configuration, tmux configuration, elevation, unrelated runtime, or unrelated tool may be modified.

Checksum enforcement, interrupted-download recovery, VCS commit pinning, exact relocation variables, architecture detection, and preservation of an existing installation all require V05 tests; they are not established by the initial supplied sources.

## Integrator follow-up

The initial source set was insufficient for several implementation details. It has now been supplemented in `bootstrap-sources/` with official environment-variable/storage/dependency pages and the actual installed **uv 0.11.7** CLI help for tool install, Python install, and tool uninstall. Prefer those exact-version flags over assumptions drawn from current web docs. No installer or release has been published, and no real user-owned runtime has been changed. Treat the initial note's unverified items as open until these added sources and isolated subprocess tests resolve them.