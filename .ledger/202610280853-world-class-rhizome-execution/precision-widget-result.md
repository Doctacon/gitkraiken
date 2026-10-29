Implemented `src/rhizome/precision_diff.py` and ledger-only harness/evidence.

- `PrecisionDiff` supports immutable document rendering, range/hunk selection, stage/unstage messages, stale/unsupported refusal, literal search, whitespace markers, word emphasis, context refresh, and responsive controls.
- Added integration contract: `.ledger/.../precision-widget.md`
- Captured and visually inspected 170×48 and 80×24 evidence.
- Validation passed: Ruff, `py_compile`, and a synthetic headless `v → down → s` message assertion.

Did not modify app integration or claim the root-owned real-Git test passes.