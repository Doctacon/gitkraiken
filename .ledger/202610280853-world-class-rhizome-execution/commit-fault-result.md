Implemented:

- `tests/test_commit_faults.py`
- `.ledger/202610280853-world-class-rhizome-execution/commit-fault-evidence.md`
- `.ledger/202610280853-world-class-rhizome-execution/commit-fault-result.md`

Validation:

```text
uv run --locked pytest -q tests/test_commit_faults.py::test_foreground_hook_child_blocks_publication_after_hook_shell_is_killed
1 failed in 1.66s
```

Reproduced the suspected gap: native Git returned `0` while the foreground child remained gated and alive, but Rhizome moved/deleted its candidate. After release in `finally`, the child completed without being killed; its late staging failed with status `128`.

Stopped at this first red. Publication fsync/rename/ownership cases and broader suites were not run. A cleanup-only zombie-sensitive PID check was removed afterward and not rerun per the stop instruction. No product code was changed.