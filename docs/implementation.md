# Implementation contract

A Python 3.13+ / Textual 1.x application, launched with `uv run gitkraiken [PATH]`.
No web server, hosted service, account integration, or token database. Existing Git
credential helpers / SSH configuration handle private GitHub and Azure DevOps access.

## Ownership / seams

- `models.py`: immutable shared values.
- `git.py`: synchronous `Repository(path)` discovers the worktree root, rejects bare
  repos with a friendly `GitError`. `root: Path`. All I/O stays here. Methods:
  - `snapshot(limit: int = 300, ref: str = "") -> Snapshot`: status, refs, remotes,
    stashes, current operation, bounded native Git graph. Empty `ref` means all refs
    plus HEAD (including detached HEAD); nonempty ref shows its ancestry.
  - `detail(oid: str) -> CommitDetail`: full commit message, first-parent changes
    (root commits work), stat. `FileChange.index` holds committed change type.
  - `diff(path: str = "", *, staged: bool = False, oid: str = "") -> str`: plaintext
    patch for worktree/index or first-parent diff of commit. Handles untracked files,
    binary files, root commits, bounded large output, literal paths and renames.
  - `execute(action: Action) -> str`: allowlisted mutations only; success text,
    `GitError` on failure. Destructive actions are confirmed by the UI. stage and
    unstage with empty paths mean all; discard REQUIRES explicit tracked file paths,
    only worktree edits (never deletes untracked files or resets the index). Commit
    only staged changes. reset-soft targets a commit and preserves working changes.
    pull = ff-only; push never force. Push with no upstream sets it on selected
    remote (`action.remote`, else sole remote; ambiguous remotes must error).
    Remotes expose a redacted push_url for confirmation; multiple push URLs are
    rejected. Explicit refspecs override broad or forceful user push defaults.
    Checkout local, remote tracking (creates local tracking branch when needed),
    or commit/tag detached. Branch create uses name + target (default HEAD) and
    checks out. Rename uses target old + name new. Safe delete uses target. Tag
    uses name + target. Stash includes untracked. Stash actions target exact ref.
    Continue/abort inspect current operation (including an external git am) and
    never invent one. UI stash requests include expected_oid to reject selectors
    that moved while the user was confirming.
  - `Repository.clone(url: str, destination: str) -> Path`: safe argv, credential
    redaction and noninteractive prompt policy, returns worktree root.
  - `GitError` has human-readable, redacted `str(error)`.
- `app.py`, `dialogs.py`, `app.tcss`: Textual UI. `GitKraikenApp(path: str | Path)`.
  Synchronous Git calls run via `asyncio.to_thread`, not on the UI loop; serialize
  mutation/refresh work and ignore stale selection detail results. No UI-to-shell
  calls. Refresh after success AND failure (conflicts can change repository state).
- `cli.py`, `tmux.py`, `demo.py`: entrypoints, opt-in tmux integration, disposable
  real-Git demo fixture. A demo is never presented as the user's repo.
  `--tmux-config` prints an opt-in prefix+g binding using the installation's Python
  path. The binding passes only client/pane/socket identifiers to `tmux.hotkey`;
  repository paths are discovered through argv-based tmux queries, not inserted
  into shell source. It opens the originating pane's repo on the originating
  client, or detaches a client in a marked GitKraiken popup session. Configuration
  is generated separately on each machine; no user config is edited automatically.

## Main screen

GitKraken-inspired, original terminal implementation: dark blue/charcoal palette,
teal active branch, colored graph lanes, compact toolbar, left refs tree, center
scrollable graph, right selected commit details / staging. Ref tree: local, remotes,
tags, stashes. Center: WIP, native merge connectors, badges, subjects, author, date,
SHA; filter/search, load-more. Branch selection filters history, explicit checkout
is separate. Worktree has staged/unstaged files, diff and commit composer. Include
context actions for commits/refs/stashes, quick command picker, shortcuts/help,
visible pending-operation and conflict controls. At narrow sizes allow panel toggles;
never silently perform a destructive operation. All repo data is plain text, not
Rich markup. Keep controls actually wired; omit unsupported feature promises.

## Safety / verification

Git is invoked with argv (no shell), end-of-options / validated revisions and
literal pathspecs. Never use remote URLs as display text without redacting
userinfo/query secrets. Network uses existing credential helpers and SSH agent,
never terminal password prompts; timeout and helpful auth failure instructions.
No automatic fetch/push and no force push, hard reset or clean. Poll status locally
when idle. UI confirms checkout, history rewrites, merges/rebases, stash pop/drop,
discard and remote writes. Tests use disposable real repositories and local bare
remotes, plus Textual pilot tests and isolated tmux smoke tests. Never mutate any
user repo during testing.
