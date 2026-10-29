Implemented bounded M1 distribution work.

### Changed
- `scripts/install.sh`
- `src/rhizome/distribution.py`
- `src/rhizome/cli.py`
- `tests/test_install.py`
- `tests/test_cli.py`
- `.ledger/202610280853-world-class-rhizome-execution/distribution.md`

Key behavior includes commit-only installation, verified uv 0.11.7 archives, isolated managed Python/tool storage, atomic revision pointers, rollback/uninstall safeguards, and explicit `--check-updates` release resolution.

### Validation
- Focused tests: **52 passed**
- Ruff: passed
- `sh -n scripts/install.sh`: passed
- `git diff --check`: passed

### Remaining gates
No live downloads, real installation, GitHub requests, publication, or candidate release testing occurred. Cross-platform and WSL2 live-install validation remains outstanding and is documented in `distribution.md`.