# Implementation contract

A Python 3.13+ / Textual 1.x application, launched with `uv run gitkraiken [PATH]`.
No web server, hosted service, built-in account management, or token database.
Existing Git credential helpers / SSH configuration handle Git access. Optional
hosted PR browsing/creation uses authenticated GitHub/Azure CLIs; see [pull requests](pull-requests.md).

## Ownership / seams

- `models.py`: immutable shared values.
- `git.py`: synchronous `Repository(path)` discovers the worktree root, rejects bare
  repos with a friendly `GitError`. `root: Path`. All Git I/O stays here. Methods:
  - `snapshot(limit: int = 300, ref: str = "") -> Snapshot`: status, refs, remotes,
    stashes, worktrees, current operation, bounded chronological history. Empty `ref` means all
    refs plus HEAD (including detached HEAD); nonempty ref shows its ancestry. The UI
    always uses the unified, empty-ref form; reference selection never filters it.
    A full lightweight author-epoch/OID scan selects the globally newest `limit`
    commits before bounded metadata loading; limiting Git's traversal first would
    miss newer ancestors with skewed author dates. The returned history is newest
    first by author instant (`%at`), with raw `%aI` dates retained for display.
  - `worktrees() -> tuple[Worktree, ...]`: local `git worktree list --porcelain -z`
    (Git 2.36+), preserving literal paths and detached/bare/locked/prunable metadata.
    The UI can open an available entry without mutating either checkout. Opening
    revalidates registration and exact discovered root; stale entries cannot fall
    back to a parent repository. Worktree creation reuses guarded context actions.
  - `open_pull_requests() -> tuple[PullRequestListing, ...]`: explicit hosted reads
    for supported fetch remotes, deduplicated by hosted identity. Each listing
    carries requests or a safe per-repository error; never called by snapshot/polling.
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
  - `pull_request_plans(action: Action) -> tuple[PullRequestPlan, ...]`: local-only
    resolution of captured current source and clicked target branches to supported
    hosted repositories. Exact upstream mappings win; ambiguous remotes require a
    picker. Same-repository only, with no implicit branch publishing.
  - `create_pull_request(plan, title, description) -> str`: revalidate context and
    repository mappings, check live remote branch existence and source HEAD, then
    call the provider adapter once. Returns the created PR's browser URL. No local
    mutation, push, or merge; failures do not erase the UI draft.
  - `Repository.clone(url: str, destination: str) -> Path`: safe argv, credential
    redaction and noninteractive prompt policy, returns worktree root.
  - `GitError` has human-readable, redacted `str(error)`.
- `graph.py`: pure `render_history(history, style)` returns `GraphRow` values with
  Rich text, the original commit identity, and physical width/height. Smooth mode
  lays out real `Commit.parents` in the supplied chronological order with stable active lane slots,
  lane colors, rounded connectors and multiline commit rows. Secondary-parent
  forks precede first-parent coalescing; joins into existing secondary-parent lanes
  use separate fork/join rows so unrelated ancestry cannot leak through a junction.
  Crossings use `╴│╶` underpasses, never connected four-way junctions. Dotted tails
  retain unloaded parents. Parents already above a child use explicit `↑ parent <SHA>`
  references instead of false downward lanes. Compact uses the same routing without
  optional spacers and flattens required connector lines into separate rows. Legacy
  native ANSI input remains supported. No Git I/O, widget state, topology clipping,
  or new dependencies.
- `sidebar.py`: compact All refs / Local / Remote / Worktrees / PRs view tabs,
  mounted-once Input + Tree over a `Snapshot`; no Git I/O. Worktree open/create
  and PR load requests are typed messages. PR results are supplied separately,
  with loading, empty, unsupported and per-repository failure states. Root/remotes
  changes invalidate hosted data; tab/filter changes preserve action context. Typed
  `SidebarSelection` messages carry full targets, OIDs, exact remote ownership,
  and repository root. Fold/cursor keys are semantic, not TreeNode IDs. A complete
  selection index is independent of search visibility. Stash identity uses OID.
  Search temporarily exposes matching ancestors without overwriting normal folds;
  refresh preserves the query and viewport, while a new repository resets them.
  Actual Click chains distinguish selection from immediate checkout; only two
  primary-button label clicks on the same branch qualify. Rebuilds invalidate
  pending double-clicks. Linked worktrees have a separate selection kind from the
  current Working directory; double-click or Open navigates to an available tree.
  PR selection opens a read-only detail modal. Right-click branch labels emit ContextRequested with an
  exact ref, repository root and screen position; other nodes/carets are inert.
  Carets, categories, Enter, and navigation never check out.
- `ref_badges.py`: pure typed pointer grouping by OID from refs, remotes, and stashes,
  not ambiguous `Commit.refs` strings. Local/HEAD and provider markers precede
  potentially truncated names. Description metadata retains full refs.
  Remote markers describe local last-fetched state, never live server state.
  Individual name spans carry full-ref metadata for graph branch context menus.
- `context_actions.py`: immutable clicked targets and pure contextual menu policy.
  Grouped literal action labels depend on local/remote/current/detached/operation
  state, not the previous inspector selection. Credential-free provider links are
  generated only for supported URL shapes and encode remote source branch names.
- `pull_requests.py`: strict hosted-repository URL parsing, immutable PR plans/listings,
  and noninteractive provider CLI adapters (`gh api`, `az repos pr create/list`). Open
  listings use pagination and validate response schemas, constructing browser URLs
  from trusted repository identity and positive PR numbers. Explicit
  repository and branch fields, literal argv/JSON payloads, bounded timeout, no
  automatic retries. Credentials remain CLI-owned; failure output is not echoed.
- `pull_request_dialog.py`: read-only PR summary, title/description composer and persistent success link
  with copy/open affordances. Modal owns in-flight controls; the app shares its
  operation lock/busy flag. Duplicate submission and cancellation are blocked until
  the request settles. Errors keep the draft; Escape cancels before submission.
- `dates.py`: presentation-only conversion of offset-aware Git timestamps to the
  OS local timezone at the commit instant (including DST). Raw model dates stay intact.
- `commit_files.py`: presentation-only changed-file tree. Compacted directory
  chains, basename/status leaves, and typed FileSelected messages. Same-commit
  refresh preserves folds and selection.
- `app.py`, `dialogs.py`, `app.tcss`: Textual UI. `GitKraikenApp(path: str | Path)`.
  Synchronous Git calls run via `asyncio.to_thread`, not on the UI loop; serialize
  mutation/refresh work and ignore stale selection detail results. No UI-to-shell
  calls. Refresh after success AND failure (conflicts can change repository state).
- `cli.py`, `tmux.py`, `demo.py`: entrypoints, opt-in tmux integration, disposable
  real-Git demo fixture. A demo is never presented as the user's repo.
  `--tmux-config` prints an opt-in prefix+g binding using the installation's Python
  path. The binding passes only client/pane/socket identifiers to `tmux.hotkey`;
  repository paths are discovered through argv-based tmux queries, not inserted
  into shell source. The hotkey swaps a real application pane into the origin's
  layout cell with `swap-pane -d -Z`. A detached parking session per origin/repo
  holds the inactive app or the original live shell/editor, so neighbors stay
  visible and native pane resizes reach Textual. Pane IDs survive swapping;
  app-pane markers record its origin and parking session. A per-origin `wait-for`
  lock serializes startup, toggles and cleanup. The app gets `remain-on-exit` and
  a pane-local `pane-died` hook before launch: quit/crash swaps back only a parked
  origin, then removes only the dead app pane (never the user's session/process).
  Cleanup does not change focus or zoom. Destructive tmux `kill-pane`/`kill-window`
  commands bypass process-exit restoration; no invasive global hooks intercept
  them. Explicit `--tmux` windows and `--popup` sessions are unchanged; the hotkey
  still detaches a client in a marked popup session. Configuration is generated
  separately on each machine; no user config is edited automatically.

## Main screen

GitKraken-inspired, original terminal implementation: dark blue/charcoal palette,
teal active branch, colored graph lanes, compact toolbar, left refs tree, center
scrollable graph, right selected commit details / staging. Sidebar tabs: All refs,
Local, Remote, Worktrees, PRs. All refs includes tags and stashes; other tabs focus
on one collection without changing graph scope. Worktrees show paths and status,
with Open/New actions. PRs load only on explicit request and expose a detail modal
with browser/copy actions. Center: WIP, Smooth/Compact graph, badges, subjects and SHA;
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
Hover tooltip popups are globally hidden, including in modal menus; normal mouse
highlighting and explicit clicks remain active. Worktree has staged/unstaged files,
diff and commit composer. Include context actions for commits/refs/stashes, quick
command picker, shortcuts/help, visible pending-operation and conflict controls.
Below 85 columns automatically hide the inspector; Ctrl+Right swaps it into the
side-panel slot and Ctrl+Left returns to refs, keeping history visible. Automatic
collapse does not erase explicit collapse or splitter-size preferences when growing
again. At 60 columns hide secondary toolbar buttons (still available in Actions).
Actions require explicit invocation but no
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
