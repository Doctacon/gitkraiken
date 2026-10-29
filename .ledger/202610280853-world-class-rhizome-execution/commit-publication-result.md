Implemented:

- `tests/test_commit_publication.py`
- `.ledger/202610280853-world-class-rhizome-execution/commit-publication-evidence.md`
- `.ledger/202610280853-world-class-rhizome-execution/commit-publication-result.md`

Validation:

```text
uv run --locked pytest -q tests/test_commit_publication.py::test_candidate_directory_substitution_after_native_git_is_not_published
1 failed in 1.01s
```

Genuine red: after native Git returned `0` and the candidate directory was replaced, Rhizome wrote to the foreign `context.txt` before recognizing changed ownership.

Stopped immediately. Later publication fault cases, Ruff, and broader suites were not run. No product code or gates changed.