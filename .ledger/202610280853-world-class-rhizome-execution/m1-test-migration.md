# M1 app-test migration

## Scope and policy

Owned changes were limited to `tests/test_app.py` and this report. The migration follows C4's no-exceptions transition rule and the enabled/gated matrix in `operations.md` and `operations-followup.md`: fetch and non-force push remain routine successes; discard without an app recovery journal, pull, rebase, reset, edit-message, apply-patch, continue/abort, and remote deletion remain fail-closed until M3 protection/recovery work exists.

The migrated tests use disposable real Git repositories and assert HEAD, refs, index entries, status, and literal file bytes as applicable. Target/context pickers remain exercised. The rebase spy on `Repository.execute` was removed.

## Red evidence

The existing full discovery log, `integration-test-run.txt`, recorded the 13 stale app expectations. I reproduced that exact slice before migration:

```text
$ uv run --locked pytest -q tests/test_app.py::test_discard_runs_without_confirmation_and_preserves_index tests/test_app.py::test_network_toolbar_actions_run_without_confirmation tests/test_app.py::test_right_click_rebases_current_feature_onto_clicked_branch tests/test_app.py::test_right_click_rebase_conflicts_can_abort_or_continue tests/test_app.py::test_graph_reset_mode_picker_executes_explicit_choice tests/test_app.py::test_context_edit_latest_message_preserves_staged_content tests/test_app.py::test_context_apply_patch_keeps_index_unchanged tests/test_app.py::test_context_remote_delete_requires_explicit_destination_and_preserves_local tests/test_app.py::test_context_remote_delete_rejects_mapping_change_while_picker_open
F.F.FFFFFFFFFFF                                                          [100%]
13 failed, 2 passed in 63.13s (0:01:03)
```

The 13 failures were discard; pull; local and remote-target rebase; externally initiated rebase abort and continue expectations; soft, mixed, and hard reset; edit-message; apply-patch; remote deletion; and changed-mapping remote deletion. Fetch and push were the two passes.

An intermediate migrated run exposed six assertion-timing errors rather than product mutations: app startup creates/synchronizes `refs/remotes/origin/HEAD`, while those assertions had captured refs before mount. The exact failures were pull, both rebase target cases, edit-message, apply-patch, and remote deletion (`6 failed, 9 passed in 63.76s`). Baselines were moved to after app settlement. The fetch fixture was also tightened so the bare remote advances only after app settlement, proving the toolbar fetch itself moves the tracking ref rather than accepting a mount-time transition.

## Migrated behavior

- Discard without an injected recovery journal reports the checkpoint gate and preserves worktree bytes, index content, HEAD, and status.
- Fetch advances a real remote-tracking ref after a post-mount bare-remote commit; non-force push advances the real bare destination. Pull leaves local HEAD, tracking refs, and content unchanged while the remote remains independently advanced.
- Rebase target menus retain exact local/remote context, but submission preserves all refs, index, status, and files.
- Continue/Abort coverage now starts a real conflicting rebase with fixture Git. Abort is attempted while conflicted; Continue is attempted after the fixture resolves and stages the conflict. Both explain the M3 gate and preserve the external rebase directory, operation state, refs, HEAD, index, status, and file bytes.
- Reset mode selection remains explicit, while all three modes preserve HEAD, branch refs, index, status, and topic content.
- Edit-message still loads the selected message, but the protected/unavailable gate preserves commit/message/tree, refs, staged bytes, index, status, and worktree bytes.
- Apply-patch still accepts the selected patch path, but preserves HEAD, refs, index, status, and worktree bytes.
- Remote deletion still displays the exact remote/source/destination picker. Ordinary and changed-mapping submissions preserve local and bare refs and content; the externally changed mapping remains intact.

## Green evidence

```text
$ uv run --locked pytest -q tests/test_app.py::test_discard_without_recovery_fails_closed_and_preserves_content tests/test_app.py::test_network_toolbar_actions_obey_m1_gates tests/test_app.py::test_right_click_rebase_is_gated_without_mutating_selected_context tests/test_app.py::test_external_rebase_conflict_finish_is_gated_without_altering_operation tests/test_app.py::test_graph_reset_picker_refuses_gated_modes_without_mutation tests/test_app.py::test_context_edit_latest_message_is_gated_and_preserves_staged_content tests/test_app.py::test_context_apply_patch_is_gated_without_mutating_worktree_or_index tests/test_app.py::test_context_remote_delete_picker_is_explicit_but_m1_refuses_deletion tests/test_app.py::test_context_remote_delete_mapping_change_remains_fail_closed
...............                                                          [100%]
15 passed in 65.27s (0:01:05)

$ uv run --locked ruff check tests/test_app.py
All checks passed!
```

`git diff --check -- tests/test_app.py` also passed.

## Limits

No production code, other test files, complete candidate suite, full app suite, provider publication, platform matrix, or M3 functionality was run or implemented. Existing direct repository/coordinator tests remain responsible for low-level Git semantics.
