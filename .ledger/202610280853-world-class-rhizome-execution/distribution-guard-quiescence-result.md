Implemented the bounded regression in:

- `tests/test_install.py`
- `.ledger/202610280853-world-class-rhizome-execution/distribution-guard-quiescence.md`
- `.ledger/202610280853-world-class-rhizome-execution/distribution-guard-quiescence-result.md`

Validation:

- Focused test: **1 failed in 3.31s**
- Second installer invocation incorrectly returned **0** while the surviving child remained active.
- The child was released and observed exiting in `finally`.
- Stopped at this first genuine red as instructed.

No production edits, live installs, real downloads, dependencies, Git configuration changes, commits, or publication. Later assertions were not reached; V05/live installation remain blocked.