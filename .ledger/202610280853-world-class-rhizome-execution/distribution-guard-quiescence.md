# Distribution guard quiescence regression

Date: 2026-10-28

Scope is limited to `tests/test_install.py`, this note, and the final result note. Production scripts are inspection-only.

## Assigned check

Add one public installer subprocess regression at the existing command-boundary fixture. The regression must kill only a disposable intermediate foreground launcher while its ordinary foreground child survives and delays payload writing. It must verify that activity remains fail-closed, a second invocation is fenced without cleanup or repopulation, and the pre-existing `current` and `previous` selections remain unchanged. The child must always be released and observed in test cleanup.

The focused regression is the only authorized validation. Stop on and report the first genuine red. No live/user installation, real download, dependency or production change, host Git configuration write, commit, publication, or acceptance claim is authorized.
