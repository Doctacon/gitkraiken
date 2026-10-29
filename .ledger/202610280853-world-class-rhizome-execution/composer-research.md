## C3 recommendation (Git v2.36.0)

### Pinned-doc facts

- `commit.template` is a pathname for a template used when **editing** a message: [`commit-config.txt:25-27`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/commit-config.txt), [`git-commit.txt:192-200`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/git-commit.txt).
- A supplied `-F <file>` message bypasses template loading; `-F -` reads stdin: [`git-commit.txt:169-172`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/git-commit.txt), [`git-commit.txt:193-200`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/git-commit.txt).
- `commit.cleanup` controls cleanup; `strip`, `whitespace`, `verbatim`, `scissors`, and `default` have distinct semantics. `core.commentChar` controls recognized comment lines and the scissors marker: [`git-commit.txt:234-260`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/git-commit.txt), [`core-config.txt:509-516`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/core-config.txt).
- Normal commits run hooks by default; `--no-verify` bypasses pre-commit and commit-msg: [`git-commit.txt:216-220`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/git-commit.txt). Git may run `prepare-commit-msg`, `commit-msg`, `pre-commit`, `post-commit`, and `post-rewrite`: [`git-commit.txt:562-566`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/git-commit.txt).
- Git rejects an ordinary commit with no staged changes, and `--allow-empty` is the explicit override: [`git-commit.txt:222-226`](.ledger/202610280853-world-class-rhizome-execution/composer-sources/git-commit.txt).

### Current implementation observations

- `Repository.execute(commit)` currently rejects blank messages, checks staged content, then invokes `git commit -m <message>`: [`src/rhizome/git.py:2104-2117`](src/rhizome/git.py).
- `RhizomeApp.commit_changes()` calls `.strip()`, losing leading/trailing multiline whitespace, then submits the result: [`src/rhizome/app.py:2163-2168`](src/rhizome/app.py).
- Draft persistence stores the editor’s exact text and retains it on failure; successful commit clears the editor: [`src/rhizome/app.py:438-456`](src/rhizome/app.py), [`src/rhizome/app.py:2213-2215`](src/rhizome/app.py).

### Recommendation

1. Resolve `commit.template` without writing configuration. The pinned excerpts establish that it is a pathname, but do **not** specify relative-path or `~` expansion rules. Treat those exact rules as **requiring real-Git v2.36 oracle tests**, rather than implementing guessed expansion.
2. Load the resolved template only when the composer has no user draft. Preserve the draft as authoritative thereafter; do not silently replace or merge it.
3. Submit the final multiline text through `git commit -F -`, preserving bytes/newlines. Do not use `-m`, `.strip()`, or application-side comment/whitespace cleanup.
4. Let Git honor `commit.cleanup` and `core.commentChar`; do not impose a mandatory message schema or cleanup policy. `-F -` still lets Git apply its configured cleanup behavior.
5. Preserve hooks and their authority: do not add `--no-verify`. Hooks may validate or rewrite the message.
6. Continue refusing commits with no staged changes. Also refuse template-only/comment-only effective content before invoking Git, but define “effective” through Git-compatible oracle tests for each cleanup mode and comment character.
7. For missing, unreadable, non-regular, or oversized templates: fail closed with a bounded, user-visible error; retain the existing draft and create no commit. The size limit and exact diagnostic are product recommendations, not pinned Git behavior.

**Unverified by the saved excerpts:** exact `commit.template` relative-path base, `~` expansion, and edge-case cleanup results for `-F -`; these should be covered by disposable real-Git v2.36 tests.