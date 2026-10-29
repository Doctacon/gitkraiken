Implemented the Git-engine composer slice in `src/rhizome/git.py` only, preserving existing transaction and terminal edits.

### Changes
- Added strict commit encoding resolution and validation.
- Added bounded, nonblocking commit-template loading with regular-file, size, mutation, NUL, and decoding checks.
- Switched commits to native `--edit --no-status --no-verbose -F -`.
- Pinned captured `i18n.commitEncoding`.
- Passed exact encoded message bytes through an anonymous regular `TemporaryFile`.
- Preserved the existing nonblank predicate, hooks, signing, terminal handoff, transaction publication, and outcome handling.

### Validation
- `uv run ruff check src/rhizome/git.py` — passed.
- `git diff --check -- src/rhizome/git.py` — passed.
- `uv run pytest -q tests/test_commit_transactions.py` — **10 passed in 9.85s**.
- `uv run pytest -q tests/test_commit_terminal.py` — **3 passed in 9.09s**.
- Remaining existing commit batches — **31 passed in 31.20s**:
  - `test_commit_faults.py`
  - `test_commit_processes.py`
  - `test_commit_publication.py`
  - `test_commit_representations.py`
  - `test_commit_files.py`

Bare `python`/`pytest` commands were unavailable on PATH; validation was rerun successfully through `uv run`.

### Coverage gaps
Per instruction, no tests were added. Existing tests do not directly cover:
- Template path expansion, symlinks, FIFO/nonregular files, size limits, mutation, NULs, or decoding.
- Cleanup-mode/comment-character byte parity and hook source `message`.
- Non-UTF-8 encoding headers, encoding pinning, or pre-transaction encoding failures.
- Exact multiline/trailing-whitespace stdin preservation.