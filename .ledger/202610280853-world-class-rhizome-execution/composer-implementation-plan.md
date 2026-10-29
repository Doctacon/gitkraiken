> Historical planning artifact. Roadmap execution has resumed in
> `composer-progress.md`; ordinary commits remain enabled. The old gating
> instructions below are superseded, and proposed test additions were not
> authorized for this increment (operator selected existing checks only).

## Resolved implementation shape

Use the existing `Action.message: str`, app draft store, coordinator gate, and reviewed-index transaction. Add only private seams; do not change `Action`, persistence schema, provider code, or any public interface.

### Native commit semantics

Submit with:

```text
git … commit --edit --no-status --no-verbose -F -
```

and provide the encoded message on stdin.

Rationale:

- `-F -` preserves multiline input without argv or `-m` transformations.
- `--edit` tells Git that Rhizome’s `TextArea` is the editor, selecting editor-mode cleanup:
  - unset/default: `strip`
  - `strip`: strip comments and whitespace
  - `whitespace`: preserve comments
  - `verbatim`: preserve message bytes
  - `scissors`: truncate at the configured marker, then whitespace cleanup
- `--no-status --no-verbose` prevents invisible status/diff text from being added after the user leaves Rhizome’s composer. This is essential for `whitespace` and `verbatim`, where generated text could otherwise enter the commit or reach hooks.
- Do not pass `--cleanup`, `--no-verify`, `--allow-empty`, `--allow-empty-message`, or signing overrides.
- Keep `GIT_EDITOR=true`; no second editor opens.
- `prepare-commit-msg` receives source `message`, as required by `-F`. Both message hooks may rewrite or reject the message.

Rhizome must send the exact editor text to stdin. Final commit text may differ only because Git cleanup or hooks changed it. With `commit.cleanup=verbatim` and non-mutating hooks, raw commit message bytes must equal the submitted bytes.

### Message admission

Preserve the existing nonblank safety gate, but stop altering accepted text:

```python
message = editor.text
if not message.strip():
    ...
Action("commit", message=message)
```

Keep equivalent predicate-only checks in the coordinator/repository. This preserves the established “user message required” gate while retaining leading/trailing whitespace in accepted messages.

Additional behavior:

- Reject an exact, unchanged template seed in the app before creating an action.
- Do not implement comment parsing or cleanup in Python.
- Comment-only behavior is Git-configured:
  - default/`strip`: Git removes comments and aborts if empty, after hooks have had authority.
  - `whitespace`/`verbatim`: comments count as content.
  - `scissors`: comments remain, but marker suffixes are truncated.
- A `prepare-commit-msg` hook may turn comment-only input into a valid message.
- The unchanged-template admission gate is intentionally earlier than hooks because `-F` clears Git’s native template identity.

## Implementation steps

### 1. Pin behavior with real-Git characterization tests

In `tests/test_commit_transactions.py`, first establish Git 2.36/current behavior using disposable repositories:

1. Compare plain `git commit -F -` with the selected `--edit --no-status --no-verbose -F -` command.
2. Assert the editor-mode cleanup matrix above.
3. Assert `core.commentChar`, including a non-`#` character and `auto`.
4. Assert scissors truncation only occurs in editor mode.
5. Set `commit.status=true`, `commit.verbose=true`, and `commit.cleanup=verbatim`; prove no hidden status or diff enters the hook input or commit.
6. Assert the `prepare-commit-msg` source argument is `message`.
7. Assert hook rewrite order and `commit-msg` rejection behavior.

Stop rather than substituting guessed behavior if Git 2.36 disagrees with these assertions.

### 2. Add a private template/encoding seam in `git.py`

Add private repository helpers, not public methods:

- `_commit_encoding()`:
  - Read effective `i18n.commitEncoding` with `git config --includes --null --get`.
  - Default to UTF-8.
  - Resolve through `codecs.lookup`.
  - Fail visibly for unsupported encodings; never guess or replace characters.

- `_read_commit_template()`:
  - Read effective path with:

    ```text
    git config --includes --null --path --get commit.template
    ```

  - Return `None` for an unset value.
  - Let Git perform `~` and `%(prefix)` interpolation.
  - Treat a remaining relative path as relative to `Repository.root`, matching the transaction’s Git working directory.
  - Open only that configured path; do not search user directories.
  - Follow a configured symlink to a regular file, matching Git.
  - Require the final descriptor to be regular and read at most `1 MiB + 1 byte`.
  - Fail on missing, unreadable, non-regular, changed-during-read, or oversized files without returning partial content.
  - Decode strictly using the effective commit encoding.

- `_encode_commit_message()`:
  - Strictly encode the final `str` with the current effective commit encoding.
  - Fail before opening the transaction if characters are unrepresentable.
  - This prevents Git from adding a non-UTF-8 encoding header to UTF-8 stdin bytes.

Use user-visible category errors such as “template exceeds Rhizome’s 1 MiB limit” or “message cannot be encoded as ISO-8859-1”; never include template/message content in diagnostics.

### 3. Integrate templates with the existing app draft lifecycle

In `app.py`:

1. Add private ephemeral state such as `_commit_template_seed: str | None` and `_template_error`.
2. Restore durable state first.
3. Define an empty saved string as “no active draft.” Any nonempty draft—including whitespace or comment-only text—is authoritative.
4. If a nonempty draft exists:
   - Load it exactly.
   - Do not access or merge the configured template.
5. Otherwise call `repo._read_commit_template()` off the UI loop:
   - Load the exact decoded text.
   - Immediately verify `TextArea.text == loaded_text`.
   - Reject mixed or otherwise unrepresentable newline forms rather than silently normalizing them.
6. Suppress the initial `TextArea.Changed` save by setting `_saved_draft` to the seed while leaving persisted `"draft"` empty. The template becomes a durable draft only after the first user edit.
7. On template failure:
   - Leave the editor/store unchanged.
   - Show a bounded error.
   - A subsequent explicit edit may become the user’s own durable draft; never silently substitute empty text.
8. In `commit_changes()`, retain the raw editor string, apply the existing blank predicate, then reject exact equality with `_commit_template_seed`.
9. Continue clearing the draft only after confirmed commit success; all Git, hook, signing, and encoding failures retain it.

No persistence migration or new stored field is needed.

### 4. Feed stdin through the native transaction

In `_CommitIndexTransaction.run()`:

- Add a private `input_bytes` parameter.
- Write message bytes to an anonymous `tempfile.TemporaryFile`, rewind it, and pass that descriptor as stdin.
- Do not use a pipe: the current no-deadline transaction must not introduce pipe deadlocks.
- Do not put message text into the retained interruption context.
- Keep all existing lock ownership, candidate-index, hook quiescence, output bounding, uncertain-outcome, created-OID, ref, and index-publication logic unchanged.

In `_commit_reviewed()`:

1. Keep conflict, staged-content, stale-state, index fingerprint, and lock checks.
2. Encode the original `action.message`.
3. Invoke the selected native command with stdin.
4. Preserve all existing success/failure settlement behavior.

### 5. Preserve capability gates

Do not change:

- `OperationCoordinator._availability()` for commit.
- The “Commit unavailable” button state.
- README claims that app commit submission is gated.
- `Action` or other public models.

`Repository.execute(Action("commit", ...))` remains the real-Git evidence seam; this work must not make app submission available.

## Required oracle assertions

Add or extend tests through only `Repository.execute` and `RhizomeApp` under Textual Pilot:

- Relative templates resolve from the repository root, confirmed against a direct Git editor capture.
- `~/template` resolves under a temporary isolated `HOME`; never use the real user home.
- Absolute and symlink-to-regular templates work.
- Missing, directory/FIFO, oversized, invalidly encoded, and unsupported-codec templates fail without hanging or replacing a draft.
- Nonempty saved drafts take precedence even when the configured template is subsequently unreadable.
- An untouched template is not persisted and is refused by the composer.
- Leading/trailing blank lines, trailing spaces, and terminal-newline presence reach Git unchanged under `verbatim`.
- Default, `strip`, `whitespace`, `verbatim`, and `scissors` results match a direct Git oracle command byte-for-byte.
- `core.commentChar` controls both stripping and scissors.
- `prepare-commit-msg` observes source `message`; hook rewrites survive, and `commit-msg` rejection leaves HEAD and the real index unchanged.
- ISO-8859-1 input produces matching raw message bytes and Git’s encoding header.
- An unrepresentable character fails before hooks/transaction and retains the draft.
- Existing lock, signing, interruption, alternate-index, sparse/split-index, no-staged-change, and app/coordinator gating tests remain green.

## Risky decisions to keep explicit

- **1 MiB template limit:** product safety bound, not Git behavior.
- **Editor-mode cleanup:** deliberately selected because Rhizome’s `TextArea` is the editor; plain `-F -` would retain normal template comments and disable scissors by default.
- **Status/verbose override:** presentation-only Git configuration is suppressed because Rhizome already displays staged context and otherwise Git would add text the user never saw.
- **Encoding policy:** strict `i18n.commitEncoding` decode/encode avoids falsely labeled bytes, but uncommon iconv names unsupported by Python fail visibly.
- **Empty saved draft:** means no active draft and permits a fresh template; all nonempty values win.
- **Hook source:** `-F -` necessarily reports `message`, not `template`.
- **Unchanged template gate:** occurs before hooks; all accepted comment-only/nonblank input otherwise reaches native hooks and cleanup.

### Critical Files for Implementation
- /Users/crlough/Code/personal/rhizome/src/rhizome/git.py - Private template/encoding helpers and exact stdin delivery through the native transaction.
- /Users/crlough/Code/personal/rhizome/src/rhizome/app.py - Template seeding, durable-draft precedence, untouched protection, and removal of destructive `.strip()`.
- /Users/crlough/Code/personal/rhizome/tests/test_commit_transactions.py - Real-Git cleanup, encoding, hook, stdin, and transaction oracle coverage.
- /Users/crlough/Code/personal/rhizome/tests/test_app.py - Pilot coverage for template display, errors, untouched behavior, and unchanged commit gating.
- /Users/crlough/Code/personal/rhizome/tests/test_workspace_state.py - Saved-draft precedence and proof that untouched template seeds are not silently persisted.