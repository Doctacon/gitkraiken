# Pull / Fetch — final local validation

## Outcome and scope

All four saved modes are implemented: Fetch All, Pull (fast-forward if possible),
Pull (fast-forward only), and Pull (rebase). Selecting a row only changes preference;
the primary button executes it. Divergent integrations and Abort require review.
Verified owned conflicts can be staged, continued or aborted across restart.
Uncertain ownership remains visible in Activity and blocks further repository mutations.

This is the explicitly authorized pull-owned exception, **not generic M3/M4 or
release acceptance**. External Continue/Abort, generic merge/rebase/reset/discard/
stash remain gated. No project commit/push, external-network validation or live
installation change was performed. Git commits/pushes in tests and captures used
disposable local repositories only.

Base HEAD is externally authored `65144077ddb763d0abfd1bb2136307fcca32e012`.
The pull slice is uncommitted. `pull-fetch/candidate.json` records changed source,
test and capture-tool hashes. Runtime source was unchanged through the final
batches; later additions were tests, formatting and documentation.

## Final passing evidence

Logs are under `pull-fetch/`. Counts below are separate batches, not inferred from
old green totals. Each batch stayed below 15 minutes; the longest took 597.84 s.

| Scope | Current Git 2.50.1 | Actual Git 2.36.0 | Log |
| --- | ---: | ---: | --- |
| Pull, safety, leases, recovery, native processes, preferences | 73 | 73 | `backend-current.txt`, `backend-git236.txt` |
| Initial integration + every merge/rebase Continue/Abort argv policy | 4 | 4 | `finish-policy-current.txt`, `finish-policy-git236.txt` |
| Real PTYs: reviewed commit and integration runner; tty/stderr hooks and real disposable-key SSH signing | 6 | 6 | `terminal-current.txt`, `terminal-git236.txt` |
| App, pull controls, existing app/workspace journeys | 99 | not run | `app-workspace.txt` |
| Added actual saved Fetch All and divergent FF-only clicks | 2 | not run | `saved-modes.txt` |
| Responsive dialogs, controls, settings, themes, plain/narrow workspace | 53 | not run | `layout.txt` |

Thus **237 disjoint targeted cases pass on current Git**, plus **83 minimum-Git
cases**. The earlier 235-case broad backend partition also passed before the final
finish-preflight additions; it is retained as `earlier-broad-backend.txt` and is
not added to those totals. It covered operations, fetch safety, general Git,
commit processes, persistence and the then-current pull cases.

Ruff lint, whole-tree formatting (136 files) and `git diff --check` pass; see
`static.txt`. README remains 41 lines / 223 words, versus the requested historical
40 / 238 baseline, and all seven local links resolve. No type-checker success is
claimed.

### Reproduction

Final backend/preference batch:

```sh
uv run pytest tests/test_pull.py tests/test_pull_safety.py tests/test_pull_leases.py \
  tests/test_recovery.py tests/test_pull_process.py tests/test_pull_preferences.py \
  -q -p no:cacheprovider
```

The four policy cases were added after the 73-case run, so this command now collects
77 cases. Their separate passing run used `tests/test_pull.py -k every_finish_pin`.
Both policy logs were rerun and replaced after the final test formatting and explicit
`zip(strict=False)` edit. `collection.txt` confirms 237 distinct current targeted
cases with no duplicates.

```sh
uv run pytest tests/test_commit_terminal.py -q -p no:cacheprovider
uv run pytest tests/test_pull_app.py tests/test_pull_controls.py tests/test_app.py \
  tests/test_operations_workspace.py -q -p no:cacheprovider
uv run pytest tests/test_workspace_dialog_layout.py tests/test_m1_visuals.py \
  tests/test_m2_theme_dialogs.py tests/test_m1_controls.py tests/test_m2_settings.py \
  tests/test_workspace_presentation.py tests/test_plain_workspace.py \
  tests/test_responsive_workspace.py -q -p no:cacheprovider
```

The app command now collects 101 cases: its original 99 plus two added saved-mode
cases, separately run with `tests/test_pull_app.py -k saved_fetch_all`.
For minimum Git, both `PATH` and `GIT_EXEC_PATH` pointed to the locally built
`/tmp/rhizome-git236.zSmABEnN/git-6cd33dceed60949e2dbc32e3f0f5e67c4c882e1e/`.
The captured logs explicitly report `git version 2.36.0`; this was not a version
mock or an installation change.

## Six-defect safety reconciliation

All of these cases ran freshly on both Git versions in the backend batches:

1. **Paused rebase eligibility:** `test_pull_safety.py::test_paused_rebase_rechecks_eligibility_but_still_allows_abort`
   covers new publication/protection, Continue refusal and successful Abort.
   `test_pull.py::test_rebase_continue_rechecks_current_protection_and_remote_publication`
   also checks discovery through new coordinators. The real app additionally
   reloads protection from Settings while paused and verifies refusal without
   disabling Abort.
2. **Current finish ref baseline:** `test_pull.py::test_conflicted_merge_continue_is_owned_across_restart`
   creates an unrelated tag before finish review, then proves completion and tag
   preservation rather than comparing against the obsolete initial baseline.
3. **Mixed conflict preimages:** `test_pull.py::test_abort_checkpoint_preserves_mixed_conflict_and_resolved_path_preimages`
   and `test_abort_checkpoint_preserves_distinct_resolved_index_and_worktree_versions`
   verify recoverable stage-0/index/worktree versions while another path is unmerged.
4. **Exact merge shape:** `test_pull.py::test_synchronous_post_merge_hook_extra_commit_is_uncertain_and_leased`
   rejects the hook-created extra HEAD commit; unrelated hook tag writes also
   retain uncertain ownership rather than being called successful.
5. **Transient replay paths:** `test_pull_safety.py::test_rebase_never_overwrites_ignored_data_in_transient_replay_paths`
   and the outgoing/future-transition cases in `test_pull.py` preserve ignored data
   even when an outgoing file was added and subsequently deleted.
6. **Whole-mutation interprocess serialization:** `test_pull_leases.py::test_cross_process_writer_guard_blocks_pull_fetch_during_checkout_hook`
   holds a real checkout hook while another process requests pull. The pull cannot
   fetch until the other writer finishes. Descendant/restart/linked-worktree lease
   cases separately prove retained common-repository blockers.

The additional four-case argv regression observes real native executions and
asserts `--no-replace-objects` and `-c submodule.recurse=false` on initial merge/
rebase and every Continue/Abort route. Replacement-commit obstruction and raw
replacement-blob recovery regressions pass as well. These are scoped regression
proofs, not a guarantee against arbitrary trusted hooks or external filesystem
writers.

## Final integration repairs and retained failures

- The broad app run initially had 95 passes and three failures. A review helper
  now waits for mounted/layout-ready controls; two external-operation tests now
  assert disabled buttons **and** prove the Actions route still refuses. The whole
  99-case partition was rerun successfully, not merely the failed assertions.
- New finish-state tests exposed missing early hidden-index refusal and normal
  untracked files being discovered only after native completion. Unsupported
  states are now refused before a finish mutation. Removing untracked files from
  the checkout permits a fresh Continue/Abort without losing the saved notes.
- The normal paused-ownership lease exception is limited to verified same-worktree
  staging/unstaging. No generic destructive operation was enabled.
- Minimum Git exposed unsupported `--no-update-refs` earlier in the work;
  `-c rebase.updateRefs=false` is used instead.
- `superseded-*.txt` retains the final integration's red evidence. Older partial
  green counts in `pull-fetch-plan.md` are historical, not final acceptance.

## Visual and interaction evidence

- `pull-fetch/rhizome-pull-menu-80-final.{png,svg}`: all four rows at 80×24.
- `pull-fetch/rhizome-pull-menu-dark.{png,svg}`: wide dark appearance.
- `pull-fetch/rhizome-pull-review-80.{png,svg}`: exact-target review with pinned actions.

The actual app tests include 80×24 and 170×48 reviews; real keyboard and last-row
mouse selection; Escape/outside dismissal without click-through; Unicode/plain
geometry; restart persistence; unchanged Git metadata on mode selection; offline
refusal; UI-staged conflict resolution; and restarted recovery detail scrolling.
Fetch All is exercised against two configured local remotes without integrating
local divergence. FF-only fetches then refuses divergence without starting an
integration.

## Remaining limits

- This is not the full-project test suite, hosted CI, a release, or broad M2/M3
  acceptance. Earlier roadmap performance misses are not waived.
- The PTY proof covers the actual native runner and signers, not a human operating
  the complete pull flow in every terminal. No new Linux/Windows/WSL/platform or
  external-provider acceptance is claimed.
- Large-history pull/recovery cost, repeated journal discovery and large bundle
  verification were not benchmarked. Do not hide uncertainty to avoid discovery
  cost; use this as a separate measured optimization task if needed.
- Hooks/filters/signers are trusted code. Writers escaping the native process group
  and arbitrary external Git/editor writes are outside cooperative serialization.
  No automatic killing, retry, rollback or unknown-lease clearing was introduced.
