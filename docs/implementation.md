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
    plus HEAD (including detached HEAD); nonempty ref shows its ancestry. The UI
    always uses the unified, empty-ref form; reference selection never filters it.
  - `detail(oid: str) -> CommitDetail`: full commit message, first-parent changes
    (root commits work), stat. `FileChange.index` holds committed change type.
  - `diff(path: str = "", *, staged: bool = False, oid: str = "") -> str`: plaintext
    patch for worktree/index or first-parent diff of commit. Handles untracked files,
    binary files, root commits, bounded large output, literal paths and renames.
  - `execute(action: Action) -> str`: allowlisted mutations only; success text,
    `GitError` on failure. Explicitly selected actions run without a second confirmation. stage and
    unstage with empty paths mean all; discard REQUIRES explicit tracked file paths,
    only worktree edits (never deletes untracked files or resets the index). Commit
    only staged changes. reset-soft preserves index/worktree; reset-mixed preserves
    working files and resets the index; reset-hard explicitly discards changes.
    Reset refuses unfinished operations/conflicts. edit-message amends HEAD only,
    using --only so staged content stays out of the commit. create-worktree uses
    one explicit destination, a resolved commit, and either a new named branch or
    detached HEAD. apply-patch reads one bounded regular file and checks/applies
    the same bytes to the worktree without staging. pull = ff-only; push never force. Push with no upstream sets it on selected
    remote (`action.remote`, else sole remote; ambiguous remotes must error).
    Remotes expose a redacted push_url for destination pickers; multiple push URLs are
    rejected. Explicit refspecs override broad or forceful user push defaults.
    Checkout local, remote tracking (creates local tracking branch when needed),
    or commit/tag detached. `Ref.remote` is the longest matching configured remote
    name, including slash-containing names. Unknown ownership remains explicit.
    Full ref namespaces cannot be shadowed by similarly named local branches.
    Remote checkout accepts exact `Action.remote` ownership and only reuses an
    existing same-named local branch with the same canonical upstream ref and
    configured upstream remote/source. New tracking branches resolve validated
    fetch refspecs in both directions; missing or ambiguous mappings fail before
    changing HEAD. Upstream config is written before creating/switching a new
    branch, so config-lock failures leave HEAD and refs alone. A failed switch
    may leave retryable config for a nonexistent branch; no destructive rollback
    or whole-config rewrite is attempted.
    Branch create uses name + target (default HEAD) and
    checks out. Rename uses target old + name new. Safe delete uses target. Tag
    uses name + target. Stash includes untracked. Stash actions target exact ref.
    Continue/abort inspect current operation (including an external git am) and
    never invent one. UI stash requests include expected_oid to reject selectors
    that moved while a picker was open. Context actions include expected_branch,
    expected_head, expected_ref and expected_oid; the engine revalidates source
    branch/HEAD and captured ref OID before mutation, including detached/unborn
    empty values. Follow-up prompts keep that context. set-upstream uses exact
    local and remote refs with validated fetch mappings. delete-remote-branch
    requires a captured OID, exact configured owner and source branch mapping,
    and one push destination. The deletion picker displays the resolved remote
    source and redacted push URL; Action.name captures that source and
    expected_push_url captures the destination, rejecting either configuration
    changing while the picker is open. An explicit single-ref deletion with an OID
    lease refuses a server branch that moved since fetch; it never also deletes locally.
  - `branch_remote_target(full_ref: str) -> tuple[str, str]`: resolve a local
    upstream or remote tracking alias to its exact configured remote and source
    refs/heads name for browser links, without network I/O.
  - `Repository.clone(url: str, destination: str) -> Path`: safe argv, credential
    redaction and noninteractive prompt policy, returns worktree root.
  - `GitError` has human-readable, redacted `str(error)`.
- `graph.py`: pure `render_history(history, style)` returns `GraphRow` values with
  Rich text, the original commit identity, and physical width/height. Smooth mode
  lays out real `Commit.parents` in topological order with stable active lane slots,
  lane colors, rounded connectors and multiline commit rows. Secondary-parent
  forks precede first-parent coalescing; joins into existing secondary-parent lanes
  use separate fork/join rows so unrelated ancestry cannot leak through a junction.
  Crossings use `╴│╶` underpasses, never connected four-way junctions. Dotted tails
  retain unloaded parents. Compact mode preserves every native Git graph row and
  its ANSI colors. No Git I/O, widget state, topology clipping, or new dependencies.
- `sidebar.py`: mounted-once Input + Tree over a `Snapshot`; no Git I/O. Typed
  `SidebarSelection` messages carry full targets, OIDs, exact remote ownership,
  and repository root. Fold/cursor keys are semantic, not TreeNode IDs. A complete
  selection index is independent of search visibility. Stash identity uses OID.
  Search temporarily exposes matching ancestors without overwriting normal folds;
  refresh preserves the query and viewport, while a new repository resets them.
  Actual Click chains distinguish selection from immediate checkout; only two
  primary-button label clicks on the same branch qualify. Rebuilds invalidate
  pending double-clicks. Right-click branch labels emit ContextRequested with an
  exact ref, repository root and screen position; other nodes/carets are inert.
  Carets, categories, Enter, and navigation never check out.
- `ref_badges.py`: pure typed pointer grouping by OID from refs, remotes, and stashes,
  not ambiguous `Commit.refs` strings. Local/HEAD and provider markers precede
  potentially truncated names. Hover text and commit details retain full refs.
  Remote markers describe local last-fetched state, never live server state.
  Individual name spans carry full-ref metadata for graph branch context menus.
- `context_actions.py`: immutable clicked targets and pure contextual menu policy.
  Grouped literal action labels depend on local/remote/current/detached/operation
  state, not the previous inspector selection. Credential-free provider links are
  generated only for supported URL shapes and encode remote source branch names.
- `dates.py`: presentation-only conversion of offset-aware Git timestamps to the
  OS local timezone at the commit instant (including DST). Raw model dates stay intact.
- `commit_files.py`: presentation-only changed-file tree. Compacted directory
  chains, basename/status leaves, literal full-path/rename tooltips, and typed
  FileSelected messages. Same-commit refresh preserves folds and selection.
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
tags, stashes. Center: WIP, Smooth/Compact graph, badges, subjects and SHA;
filter/search, load-more. Smooth is the default; `g`, the visible graph-style button,
and Actions switch styles without losing selection or search. Cache graph layout
across resize/search; preserve lane colors under selection. Multiline Smooth rows
remain one keyboard/click target per commit. Never cap graph column width: allow
horizontal scrolling for wide histories. Branch single-click only changes action
context and badge emphasis; it preserves graph data, cursor, inspected commit,
and viewport. Explicit Locate navigates to its tip. Double-click checks out immediately
(or offers a branch picker for ambiguous tips). Right-click opens an anchored,
scrollable grouped menu for the clicked branch name or commit row. Copy actions
remain available during operations; unsupported mutations are omitted. Reset modes,
worktree modes, remote deletion destinations, and ambiguous checkout choices use
follow-up pickers; names/paths use prompts, and message editing uses a multiline
editor. Escape/outside click only dismiss the menu. Checkout updates refs
and HEAD, not the scope of history. Unloaded tips have an explicit caption and do
not trigger filtering or automatic loading; Load more may reveal them, within the
5,000-commit cap. New pointers at an existing OID do not invent a graph divergence.
The inspector shows a commit title and compact local-time metadata, not the commit
body or multiline stat; that space belongs to the collapsible changed-file tree.
Full metadata remains in the header tooltip. Worktree has staged/unstaged files,
diff and commit composer. Include context actions for commits/refs/stashes, quick
command picker, shortcuts/help, visible pending-operation and conflict controls.
At narrow sizes allow panel toggles. Actions require explicit invocation but no
second confirmation. All repo data is plain text, not
Rich markup. Keep controls actually wired; omit unsupported feature promises.

## Safety / verification

Git is invoked with argv (no shell), end-of-options / validated revisions and
literal pathspecs. Never use remote URLs as display text without redacting
userinfo/query secrets. Network uses existing credential helpers and SSH agent,
never terminal password prompts; timeout and helpful auth failure instructions.
No automatic fetch/push, force push of history, or clean. Hard reset is an explicit
mode choice with discard/overwrite warnings; remote deletion is an explicit
single-destination choice protected by an OID lease. Poll status locally when
idle. UI executes selected Git actions immediately, preserving prompts for
required input and choices. Busy/modal guards prevent overlapping actions;
context menus capture their repository and exact source/target context. Tests use
disposable real repositories and local bare
remotes, plus Textual pilot tests and isolated tmux smoke tests. Trace rendered
Smooth glyphs (including underpasses) to check actual visible parent relationships,
not just a copy of input metadata. Cover crossing and octopus merges, disconnected
histories, history limits, lane reuse, randomized DAGs, and style/selection behavior.
Cover actual mouse/keyboard sidebar events, fold/search/scroll persistence,
namespace collisions, exact remote checkout, and pointer-only changes. Textual
1.0's Pilot leaks `chain` into MouseDown on `click(times=2)`; the test helper sends
fresh mouse events through the same screen routing for subsequent clicks.
Never mutate any user repo during testing.
