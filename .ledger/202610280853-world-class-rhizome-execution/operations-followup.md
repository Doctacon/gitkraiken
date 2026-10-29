# Operation coordinator follow-up

## Scope and design inputs

Owned files only: `src/rhizome/operations.py`, `tests/test_operations.py`, and this report. Read the original `operations.md`, C2/C3/C4 in `docs/design/world-class-rhizome.md`, the operation source, and the canonical patch contract. No gated operation was enabled and no app wiring was added.

## Changes

### Exact targets, execution inputs, and destinations

- Explicit tag targets are covered by the same target-OID fingerprint as checkout/branch/worktree targets. A real-ref regression moves `refs/heads/topic` after review and proves no tag is created. The source available at the start of this follow-up already contained `"tag"` in `_TARGET_OPERATIONS`, so this new regression was green on its first run rather than exposing the reported stale snapshot.
- Configuration fingerprints now use effective Git configuration across system/global/local/worktree scopes with includes enabled and origin/scope included. Only a SHA-256 digest enters coordinator state; raw values and origin paths are never surfaced in plans, consequences, activity, or errors.
- Stage, checkout, and create-branch now capture effective configuration and path attributes. Stage uses selected paths plus status-expanded descendants/rename peers. Checkout/create-branch additionally derive the exact HEAD-to-target changed-path set and fingerprint both current and target-tree (`check-attr --source`) effective attributes.
- Whole-file staging fails closed for effective conversion attributes (`filter`, `ident`, `text`, `eol`, or `working-tree-encoding`) and active `core.autocrlf` conversion rather than attempting to reproduce transformed bytes.
- Real tests prove a changed global include file cannot switch CRLF conversion after review, a newly effective `.gitattributes` filter on a directory-expanded path cannot transform staged content, an external attributes-file change cannot alter a clean checkout, and changed checkout/fetch ref destinations invalidate before mutation.

### Honest checkpoint outcome

- A checkpoint-completion failure after Git succeeds now carries an explicit uncertainty marker through the outer exception handler instead of being overwritten as `failed`.
- The fault test injects an `OSError` only at the recovery metadata `os.replace` filesystem boundary. It proves the tracked worktree discard actually happened, activity remains `uncertain`, and the original checkpoint remains active for recovery inspection.

### Precision stage/unstage coordinator integration

- `plan` now accepts the approved keyword-only seam `patch: PatchDocument | None` and `selection: tuple[int, int] | None` without changing `OperationPlan`'s public shape.
- Precision requests are valid only for stage/unstage, one exactly matching action/document path, the matching staged direction, a selectable original document, and an in-range inclusive selection containing a changed line.
- Frozen `_PendingPlan` retains the original immutable `PatchDocument` and selection. No callback or rendered/reconstructed patch text is stored.
- Submission performs ordinary coordinator identity/state revalidation under the common-repository lock, then calls `PatchService(repo).apply(original_document, start, end)` under that same lock. Whole-file and all other plans still use `Repository.execute` unchanged.
- Consequences identify the exact selected line-ID range. Activity and serialization are shared with every other coordinator mutation.
- Real-Git tests prove exact partial stage and inverse unstage index bytes, preservation of unrelated worktree/index edits, successful activity, intent validation, and rejection of a stale original document without index mutation.

## Red/green evidence

All commands ran on 2026-10-28 in disposable repositories/local bare remotes.

Initial follow-up slice before implementation:

```text
$ uv run --locked pytest -q tests/test_operations.py -k 'moved_explicit_target or effective_included_conversion or new_conversion_attributes or changed_tracking_destination or included_global_destination or checkpoint_completion_failure'
FFF.FF                                                                   [100%]
5 failed, 1 passed, 19 deselected in 4.47s
```

The failures were the included conversion change, new conversion attributes, checkpoint uncertainty overwrite, checkout destination, and included global fetch destination. The explicit-tag regression was the one pass because the checked-out source already fingerprinted tag targets.

After the fixes:

```text
$ uv run --locked pytest -q tests/test_operations.py -k 'moved_explicit_target or effective_included_conversion or new_conversion_attributes or changed_tracking_destination or included_global_destination or checkpoint_completion_failure'
......                                                                   [100%]
6 passed, 19 deselected in 4.56s
```

The clean-checkout target-attribute and precision-coordinator slice was added after the bounded integration request. Before implementation:

```text
$ uv run --locked pytest -q tests/test_operations.py -k 'precision or external_target_attributes'
FFFF                                                                     [100%]
4 failed, 25 deselected in 1.81s
```

After implementation:

```text
$ uv run --locked pytest -q tests/test_operations.py -k 'precision or external_target_attributes'
....                                                                     [100%]
4 passed, 25 deselected in 4.57s
```

Final assigned validation:

```text
$ uv run --locked pytest -q tests/test_operations.py tests/test_recovery.py
.......................................                                  [100%]
39 passed in 39.46s

$ uv run --locked ruff check src/rhizome/operations.py tests/test_operations.py
All checks passed!
```

## Remaining limitations

- Effective configuration is intentionally conservative: an unrelated effective config change can invalidate a reviewed plan.
- Coordinator whole-file staging refuses supported-looking conversion cases rather than calculating transformed index bytes. Users must use an external Git workflow; precision staging retains the stricter `PatchService` representation gates.
- Target attribute review depends on Git's `check-attr --source` support. An unsupported Git fails closed during planning.
- Filesystem failure after Git success cannot be reconciled automatically. The result remains uncertain and the active checkpoint is retained; the coordinator does not retry or claim rollback.
- Cross-process Git exclusion is not claimed beyond Git's own locking/transaction boundaries. Coordinator scheduling remains in-process and common-repository scoped.
- Precision coordinator requests remain one selectable document/path and one contiguous inclusive ID range. Unsupported patch representations retain their whole-file/external handoff.
- Root-owned `app.py`/`git.py` wiring, the full suite, cross-platform validation, and human validation were not performed in this follow-up.
