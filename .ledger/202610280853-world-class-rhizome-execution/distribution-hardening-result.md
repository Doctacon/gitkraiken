Implemented the bounded installer-safety follow-up.

### Changed
- `scripts/install.sh`
  - Isolated credential/config environment.
  - Added canonical root-wide transaction lock and safe stale recovery.
  - Tightened uninstall ownership checks.
  - Removed public private-uv wrapper.
- `src/rhizome/persistence.py`
  - Prevented state creation or mutation beneath managed installations, including symlink aliases.
- `tests/test_install.py`
  - Added environment isolation, concurrency, recovery, uninstall, and wrapper-removal coverage.
- `tests/test_persistence.py`
  - Added actual-filesystem managed-root and symlink tests.
- `.ledger/202610280853-world-class-rhizome-execution/distribution-hardening.md`
  - Recorded exact red/green results and remaining limits.

`src/rhizome/distribution.py` and `tests/test_cli.py` required no changes.

### Validation
- Focused tests: **47 passed**
- Ruff: **passed**
- `sh -n scripts/install.sh`: **passed**
- Per-file whitespace checks: **passed**

No live install, publication, dependency change, or platform claim was made. Remaining interruption and payload-identification limits are documented in the ledger note.