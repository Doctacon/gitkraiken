Implemented the bounded fetch-scope repair.

### Changed
- `src/rhizome/git.py`
  - Added private `_validate_fetch_scope()` planning seam.
  - Validates effective refspecs, tag pruning, symbolic destinations, inherited config, and fetch-all skip settings.
  - Pins validated refspecs using `--refmap=`, `--no-force`, and `--no-prune-tags`.
  - Preserves safe cache pruning, negative/source-only refspecs, and default tag auto-follow.
- `tests/test_fetch_safety.py`
  - Added sequential disposable real-Git safety and compatibility coverage.
- `.ledger/202610280853-world-class-rhizome-execution/fetch-safety-evidence.md`
  - Recorded red-to-green evidence and scope limits.

### Validation
- Initial red: `10 failed, 6 passed`
- Final: `22 passed in 13.56s`
- Ruff: `All checks passed!`
- Host: Darwin arm64, Git 2.50.1, Python 3.13.0

Full suite and UI/coordinator validation were not run. Coordinator/UI fetch gates remain unchanged. No M1 readiness claim.