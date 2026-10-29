# M1 distribution implementation evidence

## Scope and result

Implemented the bounded C10/V05 distribution slice on 2026-10-28 without publishing, committing, installing into the real home, or contacting GitHub/provider APIs.

Owned implementation:

- `scripts/install.sh` — POSIX bootstrap with mandatory full lowercase 40-hex `--revision`, pinned/verified private uv 0.11.7 archives, isolated managed Python/tool storage, per-revision installs, atomic pointer files, explicit rollback, and marker-gated uninstall.
- `src/rhizome/distribution.py` — explicit GitHub public-release query and immutable release-tag target resolution.
- `src/rhizome/cli.py` — standalone `--check-updates` mode only; no default traffic.
- `tests/test_install.py`, `tests/test_cli.py` — isolated subprocess/public-CLI coverage.

No application, Git adapter, model, packaging metadata, README, or workflow file was changed in this slice.

## Source and pin evidence

- Project source: `https://github.com/Doctacon/rhizome` (repository README).
- Design/validation: `docs/design/world-class-rhizome.md` C10 and `docs/design/validation-plan.md` V05.
- uv documentation/help inputs: the official records under `bootstrap-sources/`, including `uv-release.json`, storage/environment documentation, and captured uv 0.11.7 `python install`/`tool install` help.
- Local exact-version confirmation: `uv 0.11.7 (Homebrew 2026-04-15 aarch64-apple-darwin)`. Its actual help exposed `--python`, `--managed-python`, `--no-python-downloads`, `--no-bin`, `--install-dir`, and `--no-config`; the bootstrap uses only supported relevant flags.
- uv release archives are selected only from `https://github.com/astral-sh/uv/releases/download/0.11.7/` and matched against the six supplied official SHA-256 values before extraction or execution.
- Rhizome is installed from `git+https://github.com/Doctacon/rhizome.git@<40-hex-commit>`. `main`, `latest`, tags, and abbreviated revisions are not accepted as install pins.
- A release check resolves `tag_name` through GitHub's tag-ref/object API to a 40-hex commit. It never treats moving `target_commitish` as an install pin.

A checksum establishes equality with the digest in the supplied GitHub release metadata; it does not independently prove publisher identity or make the download chain a complete trust model.

## Persistent effects and safety behavior

Default installation data is private user storage under `${XDG_DATA_HOME:-$HOME/.local/share}/rhizome`; tests use `RHIZOME_INSTALL_ROOT` only in isolated temporary homes.

Each revision owns separate `uv`, `tools`, `bin`, `python`, `python-bin`, and `cache` directories. The script exports isolated `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_PYTHON_BIN_DIR`, and `UV_CACHE_DIR`, plus `UV_NO_MODIFY_PATH`, managed-Python, and no-config controls. It provisions Python 3.13 first, then installs the tool with automatic Python downloads disabled.

`current` and `previous` are strictly validated pointer files replaced by same-directory rename. A new current is published only after uv execution, managed-Python provisioning, tool installation, executable verification, and revision marking. A failed new install leaves the old current usable and removes its positively marked incomplete revision. Updates and rollback are explicit and do not restart running processes.

The stable launcher is inside the marked install root and prints as an absolute direct launch path. The bootstrap also prints a direct-uv alternative and exact rollback/uninstall commands. It does not invoke sudo, alter a shell profile/PATH, edit Git/tmux configuration, modify system/existing uv, or use `--force` to replace unrelated tools.

Uninstall refuses symlinked/unmarked roots and removes only a root carrying the exact app ownership marker. Rhizome state is required to remain outside that root; drafts/recovery state is reported and preserved.

## Automated evidence

Red run before implementation:

```text
uv run pytest -q tests/test_install.py tests/test_cli.py
7 failed, 4 passed
```

Final focused run:

```text
uv run pytest -q tests/test_install.py tests/test_cli.py tests/test_cli_tmux.py
52 passed in 7.26s
```

The bootstrap tests run `sh scripts/install.sh` as a subprocess with an isolated HOME/XDG state, a path containing spaces, and stubbed OS/download/checksum/archive/uv boundaries. They cover strict revision rejection, checksum-before-extract/run, macOS ARM64/x86_64 and Linux ARM64/x86_64 archive selection (glibc plus x86_64 musl), unsupported-platform refusal, isolated uv arguments/directories, interrupted update preservation, update, rollback, launcher execution, marker-gated uninstall, and preservation of drafts/unrelated files.

The public CLI tests cover mode conflicts, no request from another mode, annotated-tag-to-commit resolution, pinned command output, honest missing-release output, and clear network failure. No live request was made.

Static checks:

```text
sh -n scripts/install.sh
uv run ruff check src/rhizome/distribution.py src/rhizome/cli.py tests/test_install.py tests/test_cli.py
All checks passed!
git diff --check -- scripts/install.sh src/rhizome/distribution.py src/rhizome/cli.py tests/test_install.py tests/test_cli.py
(no output; passed)
```

Host used for these checks: macOS 26.6.2 ARM64, Apple Git 2.50.1, uv 0.11.7. No real installation directories or global configuration were changed.

## Remaining gates (not claimed passed)

- No Rhizome candidate release exists and no commit/release/publication was authorized, so no real candidate commit was downloaded or installed.
- Official uv archives and managed Python were not downloaded live; checksum success/extraction/uv behavior used controlled boundary doubles. A live clean-machine transcript remains required.
- Real install/runtime coverage remains required on macOS ARM64 and x86_64, Linux ARM64 and x86_64, musl where claimed by archive selection, and WSL2. This host result does not satisfy V06.
- Restricted-permission, abruptly killed process, proxy/TLS, offline uv resolution, and real disk-full behavior still need live/disposable-environment transcripts beyond the deterministic failure seams covered here.
- A future published release must exercise the real GitHub release/tag API response, including lightweight and annotated tags, before release readiness is claimed.
- Git/tmux minimum-version and full platform/runtime evidence remain V06 gates. V05/V06 are not marked passed by this bounded implementation alone.
