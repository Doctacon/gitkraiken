Implemented the M1 evidence/tooling slice only.

### Changed

- `scripts/benchmark_m1.py`
- `.github/workflows/ci.yml`
- `docs/validation/m1-performance.md`
- `docs/validation/m1-compatibility.md`
- `docs/validation/m1-usability.md`
- `.ledger/.../m1-performance-20261029.json`
- `.ledger/.../m1-compatibility-git236-smoke-20261029.json`

### Reference benchmark

Command:

```sh
uv run python scripts/benchmark_m1.py --samples 5 --warm-runs 1 \
  --output .ledger/202610280853-world-class-rhizome-execution/m1-performance-20261029.json
```

Verified full envelope:

- 100,000 commits
- 20,000 tracked files
- 1,000 refs
- 200 changed files
- 203,371-byte ordinary diff
- App presented the known path as 203,431 canonical bytes / 2,514 lines in every sample
- No remotes; isolated disposable HOME/config/state/credentials

Results:

- Feedback proxy p95: **7.24 ms — met ≤100 ms**
- Large-diff presentation max: **316 ms — met ≤500 ms**
- Process-cold/cache-warm startup max: **2.269 s — failed ≤1 s warm**
- Same-process warm refresh max: **2.647 s — failed ≤1 s warm**
- OS-cache-cold ≤2 s: **not established**; caches were not dropped
- Peak RSS: **92.6 MiB**
- Proposed, not approved: 128 MiB RSS; 1.2 CPU-s startup/refresh; 0.25 CPU-s diff

The evidence identifies app startup/refresh processing above the direct `Repository.snapshot` cost as the bottleneck area. No production optimization was attempted.

### Compatibility and CI

- Git 2.36.0 reduced isolated benchmark: passed.
- tmux 3.2 checksum-verified temporary source build and startup/window-reuse smoke: passed.
- Current tmux 3.5a startup/window-reuse smoke: passed.
- Existing popup smokes remain incompatible with explicit-checkout behavior.
- Pane smoke remains red on reopen/remote refresh.
- Deferred installer regression rerun:

```sh
uv run pytest \
  tests/test_install.py::test_killed_intermediate_launcher_retains_activity_until_child_exits -q
```

Result: **1 failed in 3.05s**; retry incorrectly succeeded while the child survived.

CI now has independent Linux/macOS current jobs, a normal complete-suite job, exact Git 2.36.0/tmux 3.2 source-built boundary coverage, and a clearly non-acceptance benchmark smoke. No skip/xfail was added.

### Validation

- Ruff check/format: passed
- Python compile: passed
- `git diff --check`: passed
- Workflow parsed successfully as YAML
- Final full-reference and Git 2.36 evidence assertions: passed
- CI itself was not triggered

Still pending: hosted CI results, Linux/WSL2/SSH and macOS x86_64 cells, real-terminal paint/input/clipboard coverage, installer lifecycle validation, OS-cache-cold performance, and the five-real-user pilot. No participants or usability results were invented. No commits, pushes, releases, or live installs were performed.