Implemented M1 fail-closed test migration.

**Changed**
- `tests/test_app.py`
  - Migrated all 13 stale unsafe expectations.
  - Preserved real fetch/push success coverage.
  - Added real Git ref/index/content no-mutation assertions.
  - Retained picker/context coverage.
  - Replaced rebase spy with real repository assertions.
  - Added external conflicting rebase Continue/Abort gating coverage.
- `.ledger/202610280853-world-class-rhizome-execution/m1-test-migration.md`
  - Recorded red/green evidence and scope limits.

**Validation**
- Focused migrated tests: **15 passed in 65.27s**
- `uv run --locked ruff check tests/test_app.py`: **passed**
- `git diff --check`: **passed**

No production code, other tests, full suite, or M3 functionality was changed or validated.