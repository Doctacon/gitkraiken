Implemented the distribution safety follow-up.

### Changed
- `scripts/install.sh`
- `scripts/install-runtime-constraints.txt`
- `scripts/install-build-constraints.txt`
- `src/rhizome/distribution.py`
- `tests/test_install.py`
- `tests/test_cli.py`
- `.ledger/202610280853-world-class-rhizome-execution/distribution-followup.md`

Key outcomes include pinned runtime/build artifacts, CPython 3.13.13, ambient configuration rejection, noninteractive Git access, physical state/root validation, foreign-content refusal, interruption recovery, journaled rollback, and isolated private-uv launching.

### Validation
- Focused tests: **27 passed**
- Ruff: **passed**
- `sh -n scripts/install.sh`: **passed**
- `git diff --check`: **passed**
- Offline uv 0.11.7 constraint probe reached the exact pinned artifact; retrieval intentionally failed because networking and cache access were disabled.

### Remaining evidence limits
No live uv/Python/PyPI/GitHub installation, TLS/proxy integration, or real console-entry-point execution was performed. Boundary doubles and limitations are documented in the ledger note.