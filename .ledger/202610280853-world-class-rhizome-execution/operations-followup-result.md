Implemented the bounded operations follow-up.

### Changed
- `src/rhizome/operations.py`
  - Effective config and attribute fingerprinting.
  - Target-path attribute validation.
  - Precision stage/unstage coordinator integration.
  - Preserved uncertain checkpoint outcomes.
  - Exact target/destination revalidation.
- `tests/test_operations.py`
  - Added real-Git regressions for all requested cases.
- `.ledger/202610280853-world-class-rhizome-execution/operations-followup.md`
  - Recorded exact red/green evidence and limitations.

### Validation
- `uv run --locked pytest -q tests/test_operations.py tests/test_recovery.py`
  - **39 passed**
- `uv run --locked ruff check src/rhizome/operations.py tests/test_operations.py`
  - **Passed**

No gated actions were enabled. App wiring and root-owned files remain untouched.