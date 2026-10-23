# GitKraiken

A GitKraken-inspired Git workspace for your **terminal and tmux**, built with Python
and Textual. Local-first, mouse-friendly, keyboard-driven, with a high-contrast
light palette for bright environments. Works with private GitHub and Azure DevOps
repositories through your existing Git authentication.

![GitKraiken showing a real repository's branches, commit graph and diff](docs/assets/workspace.png)

## Run

For **macOS or Linux (including WSL2)**. Requires **Git 2.31+**, **Python 3.13+**, and
[uv](https://docs.astral.sh/uv/).
A true-color terminal at **120×40 or larger** is recommended; 170 columns gives
all three panels room. Smaller tmux panes adapt automatically: below 85 columns,
the inspector collapses; use `Ctrl+Right` to show it or `Ctrl+Left` to return to
refs. Either side panel can remain alongside history at 60 columns. No special font is required.

```sh
git clone https://github.com/Doctacon/gitkraiken.git
cd gitkraiken
uv sync
uv run gitkraiken /path/to/repository

# Explore a disposable REAL Git repository with a local-only remote.
# Nothing is cloned from or pushed to an external service.
uv run gitkraiken --demo

# Clone, then open (destination must be new or empty)
uv run gitkraiken --clone git@github.com:ORG/REPO.git ~/work/repo
```

To make `gitkraiken` available from any directory:

```sh
uv tool install .
gitkraiken ~/work/repo
```

To update a checkout-based tool installation, quit any running GitKraiken workspace
with `Ctrl+Q`, then run `git pull --ff-only` and `uv tool install --force --reinstall .`
in this checkout. Reopen it normally (or with your existing tmux hotkey).

`uv run main.py [path]` and `uv run python -m gitkraiken [path]` also work. A path
inside a worktree is enough; GitKraiken discovers its root. Bare repositories are
intentionally rejected.

## The main screen

- **Left:** a searchable reference tree. Collapse Repository, Local, Remotes,
  individual remotes, and branch namespaces such as `feature/jlott` using
  their carets. Tags and Stashes also collapse. Search reveals matching paths;
  clearing it restores your folds.
  **Single-click selects action context without moving or filtering the graph.**
  **Double-click a local/remote branch label to check it out immediately.**
  **Right-click a branch for checkout, integration, creation, reset, upstream,
  rename/delete, pull request creation, and copy actions.**
- **Center:** a colored commit graph with rounded bends, branch/tag labels,
  authors, date/time, subjects and SHAs. Dates show `YYYY-MM-DD HH:MM` in your computer's
  local timezone, including daylight-saving rules for that date.
  Working changes and connector-only rows have no author or date. Smooth mode draws Git's
  actual parent relationships; Compact mode retains Git's native lane layout.
  Search by message, author, ref or SHA and
  jump between matches **without removing graph connections**. Load history in
  batches of 300, up to 5,000 commits. History stays unified across branch
  selections and checkouts; sidebar search never changes graph topology.
- **Right:** selected commit's title and compact author/date/SHA, an expanded
  changed-file tree, and a syntax-colored unified diff. Diff previews hide redundant
  `diff --git`, index, and `---` / `+++` path headers, while keeping hunk locations
  and binary, rename, or mode-change summaries. Fold directories with a
  click or Space; file labels show just the filename. Commit descriptions do not
  consume the file list's space. Merge commits are compared to their first parent.
  The **Working directory** tab has separate staged/unstaged entries,
  file/all staging, safe discard, diff preview, and a multiline commit composer.
- **Toolbar / Actions:** fetch, fast-forward-only pull, push/publish branch,
  checkout, create/rename/safe-delete branch, merge, rebase, cherry-pick, revert,
  soft reset, create/delete local tags, stash/apply/pop/drop, and open another repo.
  **Right-click menus** additionally offer worktree creation, all three reset modes,
  latest-commit message editing, upstream configuration, remote branch deletion,
  patch application, and copying names, SHAs, and supported provider links.
- **Conflicts:** a visible operation banner with Continue/Abort. Resolve files in
  your editor, stage resolutions, then Continue. Git's errors remain visible;
  failed operations refresh the screen too.

**Resize the workspace:** click, hold, and drag the vertical dividers beside the
sidebar and inspector. Drag the horizontal divider between files and the diff in
either inspector tab to adjust their heights. Drag a `│` at the right edge of a
graph or working-directory column header to change that column's width; scroll
horizontally to reach off-screen headers. Sizes are remembered for the running
session across refreshes, graph-style changes, and hiding/showing panes. Shrinking
the terminal temporarily limits pane sizes; expanding it restores your preference.
The graph column cannot shrink enough to hide lanes.

Hover tooltip popups are disabled throughout the workspace and menus.

Local state refreshes every five seconds while no dialog or operation is active.
Network requests happen **only when you ask**. Large patches are clipped with a
visible notice; binary files have a summary, not a binary-patch dump.

### Branch pointers and navigation

The graph distinguishes **💻 local branch / HEAD**, **🐙 GitHub**, **☁ Azure DevOps**,
**🦊 GitLab**, and **↗ other remote** pointers, plus **◇ tags** and **▣ stashes**.
Both local and remote markers appear on a commit when their tips agree. A local
commit moves only its local pointer; Fetch updates last-fetched remote-tracking
pointers. These are not live server indicators. Icon appearance depends on your
terminal font.

Creating a branch at an existing commit adds a pointer, not an invented graph
fork. New commits create divergence only when their actual ancestry diverges.
Use **Actions → Locate … in unified graph** to navigate to a selected tip.
If it is not loaded, the caption says so; **Load more** can reveal it explicitly.
Selecting a branch never automatically loads more history.

Double-click a commit row (including its graph node) to check it out immediately.
A branch at that commit is preferred over detached HEAD, with local branches
preferred over remote-tracking refs. If several branches qualify, choose one from
a picker (Escape cancels). A commit without a branch tip checks out **detached HEAD**.
Single-click and Enter still only inspect.

Remote checkout uses the exact selected remote ref. An existing same-named local
branch is reused only if its upstream is that exact ref on that remote; a branch tracking another
remote (or no upstream) is not silently substituted.

### Right-click branch and commit actions

Right-click a branch in the sidebar or its **name badge** in the graph for branch
and tip-commit actions. Right-click elsewhere on a commit row for actions on that
exact commit, independently of the inspector's previous selection. Working-directory
and connector-only rows do not open a commit menu. Menus scroll in small terminals;
use arrows and Enter, or click. Escape and outside clicks cancel.

- **Checkout**, **create branch here**, **create worktree here**, and **create tag**
  use the clicked tip/commit. Branch creation checks out the new branch. Worktree
  creation asks for a new branch or detached HEAD, then a destination directory;
  it leaves the current checkout alone. Relative paths start at the repository root.
- **Merge**, **rebase**, **cherry-pick**, and **revert** act on the checked-out branch.
  **Reset** asks for a mode: Soft preserves the index and working files; Mixed keeps
  working files and resets the index; **Hard discards staged/unstaged changes and can overwrite
  obstructing untracked files**. Choosing a mode executes it immediately.
- **Edit latest commit message** is available only at HEAD on a branch. The editor
  preserves multiline messages; staged files are not included in the amendment.
  This rewrites the commit SHA. Editing older messages, dropping commits, and moving
  commits require an interactive history-editing workflow and are not offered here.
- **Start a PR against … from …** creates a GitHub or Azure DevOps pull request from
  the checked-out feature branch into the right-clicked target branch. A composer
  asks for a title and optional multiline description; **Done** creates it and
  displays a browser URL with **Copy URL**. Push your commits first. Reviewers,
  work items, and tags can be added in the web UI. This optional feature uses an
  authenticated `gh` or `az` CLI; see [setup and branch rules](docs/pull-requests.md).
- **Pull** (fast-forward only) and **Push** (never force) appear on the current local
  branch, not an unrelated clicked branch. **Set upstream** on a local branch asks
  for an exact remote-tracking branch; on a remote branch it sets the current local
  branch's upstream. Setting upstream does not fetch or push.
- **Rename** affects the clicked local branch. **Delete local branch** is merged-only
  and is not offered on the current branch. **Delete on remote** asks you to select
  the explicit remote destination, then deletes only that remote branch, provided
  it still matches its last-fetched SHA. It never also deletes the local branch.
  Fetch and review again if the server branch has changed.
- **Apply patch** asks for a patch file, checks it, and applies it to the current
  working directory without staging or committing. It is not `git am`.
- Copy branch names, full commit SHAs, or browser links for GitHub.com, GitLab.com,
  and Azure DevOps. Remote link pickers do not contact a server. Branch links use
  the remote source name, including custom fetch mappings. Unsupported hosts have
  no generated link. Copy actions use native clipboard tools when available, so
  local copying does not require OSC 52. See [clipboard support](#clipboard-support).

Menus omit inapplicable actions. During an unfinished operation, copy actions stay
available; use the existing Continue/Abort controls to finish it. Captured branch,
HEAD, and target guards reject stale mutations, including changes made externally
while a follow-up name, path, or destination prompt is open. AI features and
combined (non-atomic) local-plus-remote deletion are not included.

### Clipboard support

**Copy URL**, branch-name, SHA, and browser-link actions use the system clipboard:

- **macOS:** built-in `pbcopy`, including when running locally inside tmux.
- **Linux / Wayland:** `wl-copy` from `wl-clipboard`.
- **Linux / X11:** `xclip` or `xsel`, explicitly using the clipboard selection.
- **WSL2:** Windows `clip.exe` when available through WSL interop.

These commands must be available on `PATH`. Missing, failed, or timed-out helpers
fall back to the terminal's OSC 52 clipboard request. SSH sessions use OSC 52
rather than modifying the remote host's clipboard. This fallback may need enabling
in your terminal or tmux; the app asks you to **paste to verify** because terminal
requests cannot be confirmed. Only a successful native helper reports **Copied to
system clipboard**. If copying is unavailable, the created PR link stays visible
and can be opened in your browser to copy from its address bar.

### Rebase your feature branch onto an updated base

While on your feature branch, right-click `test` (or another local/remote branch)
and choose **Rebase feature/your-branch onto test**. Your feature commits are
replayed on top of that branch; the clicked branch is not checked out or moved.
There is no additional confirmation. Escape or clicking outside dismisses the menu
without changing Git state.

The target is the local branch or last-fetched remote-tracking branch you clicked.
Update `test` first, or Fetch and choose `origin/test`, when you need newer server
commits; rebase does not fetch or stash automatically. Commit or stash working
changes first. If conflicts occur, resolve and stage them, then use Continue, or
Abort to restore the pre-rebase state. If the branch or target changes while the
menu is open, refresh and choose the action again.

### Graph styles

Press **g** or click **Smooth [g] / Compact [g]** above the graph to switch:

- **Smooth** (default): rounded Unicode bends, separated lanes, consistent colors
  along each active lane, and more space between commits. Active lanes do not
  shuffle sideways just because a neighboring branch ends. Each commit and its
  outgoing connectors form one selectable row, so arrows / `j` / `k` move directly
  between commits rather than stopping on empty connector lines.
- **Compact**: the original, denser native Git graph with diagonal connectors.
  Useful when you want to see more commits at once.

In Smooth mode, `─╴│╶─` is a crossing: the horizontal line passes **behind** the
vertical lane without joining it; a reminder appears above graphs with crossings.
Rounded forks/joins represent real parent connections. Dotted tails (`┊`) continue
beyond the loaded history, not to an
invented root. Wide graphs scroll horizontally rather than clipping away lanes.

This uses ordinary terminal characters, not image protocols or a special font,
so it works in tmux and Windows Terminal/WSL. It is smoother routing, not a
pixel-rendered Bézier graph. The choice lasts for the running workspace, including
when its tmux pane is hidden or its explicit popup is detached.

### Controls

| Key | Action |
| --- | --- |
| Click / arrows / `j`, `k` | Select history or files |
| `/` or `f` | Search history |
| Enter in search / **Next** | Next matching commit |
| `g` | Switch Smooth / Compact graph (not tmux prefix + g) |
| Sidebar click / Enter | Select a reference without moving the graph |
| Sidebar double-click | Check out a local/remote branch immediately |
| Sidebar / graph branch-name right-click | Branch and tip-commit actions |
| Graph row right-click | Actions on the clicked commit |
| Graph row double-click | Check out a branch at that commit, or detached HEAD |
| Tree caret / Space | Collapse or expand references or changed-file directories |
| `Esc` | Clear focused sidebar search; otherwise clear graph search/ref selection; cancel a dialog |
| `Ctrl+P` or `x` | Actions appropriate to the selected ref/commit/stash |
| `Ctrl+O` | Open a local repository |
| `Ctrl+R` or `r` | Refresh |
| `Ctrl+B` | Create a branch |
| `Ctrl+Left`, `Ctrl+Right` | Hide/show refs or inspector for more space |
| `?` | Help |
| `Ctrl+Q` | Quit |

Ordinary letter shortcuts do not steal input while you search or write a commit message.
Folds and sidebar search survive local refreshes; opening another repository resets them.
Git actions run as soon as you select them, without a **Confirm Git Action** screen.
Name/message prompts and destination pickers remain where input is needed. Reset
modes and remote deletion destinations are explicit follow-up choices, not implicit defaults.
Discard immediately removes the selected file's unstaged edits. Only staged changes
are committed.

## tmux

tmux **3.2+** is optional. The design follows
[apple-pi's current Textual-in-tmux approach](docs/research.md), without requiring
Pi, a plugin, global key bindings, a web server, or changes to `~/.tmux.conf`.

```sh
# Inside tmux: open/reuse this repo's window in the current session.
# Outside tmux: attach a dedicated persistent session.
uv run gitkraiken --tmux ~/work/repo

# Inside tmux: persistent popup over your current work.
uv run gitkraiken --popup ~/work/repo
```

In a popup, use your normal tmux **prefix + d** to detach; reopening resumes the
same workspace. `Ctrl+Q` exits it. Custom tmux sockets are supported.

### Install the `prefix + g` hotkey

After installing with `uv tool install .`, run these **once on the machine where
you will use tmux**:

```sh
# Append a binding for this installation; no changes are made until you redirect it.
"$(uv tool dir --bin)/gitkraiken" --tmux-config >> ~/.tmux.conf

# Reload if tmux is already running; otherwise it loads the binding on next start.
tmux source-file ~/.tmux.conf
```

Use your configured prefix (normally **Ctrl+B**, release, then **g**):

- In a repository pane, **prefix + g** opens/resumes GitKraiken **in that pane's
  exact space**. Other panes remain visible and interactive; normal tmux pane
  resizing immediately resizes the app.
- **prefix + g** again returns to your original shell/editor without losing the
  app's state. The original process stays running while the app is visible.
- **Ctrl+Q** quits GitKraiken and restores the original pane. App-process crashes
  also restore it. Use these controls rather than tmux's destructive `kill-pane`
  or `kill-window` commands, which remove the pane itself.
- Each originating pane/repository pair has its own workspace, so opening the
  same repo from two panes does not move or resize the other workspace.
- It uses the pane's current directory, including subdirectories of a repo.
- Outside a repository it shows an error on the invoking client's status line.

The toggle parks the inactive pane in a detached `gitkraiken-pane-…` session.
Do not kill that session while it holds your shell/editor. Quitting the app
cleans it up automatically. If the original process exits while parked, there is
no process to restore. `--popup` remains an explicit full-client overlay option;
inside an explicit popup, **prefix + g** still detaches it. Normal **prefix + d**
in pane-local mode detaches your entire tmux client, as usual.

Existing generated hotkeys pick up this behavior after updating the installation
and quitting/reopening old workspaces. No binding change is required unless the
installation's Python path changed.

The generated binding contains the absolute Python path, so it works even when
an existing tmux server cannot find `gitkraiken` or `uv` on its `PATH`. It targets
the invoking client/pane/socket explicitly. It **does override an existing
prefix + g binding** when loaded, but does not change your prefix or other keys.
Remove the generated `bind-key` line and run `tmux unbind-key -T prefix g` to
remove it (then restore any previous binding).

For checkout-based development, `uv run gitkraiken --tmux-config` generates the
same binding using that checkout's `.venv`. Regenerate the binding if you move or
remove that environment. Do not copy a generated macOS binding to WSL: generate
it in WSL after installation.

### WSL2 quick setup (Ubuntu/Debian)

Run inside your **WSL Linux shell**, not PowerShell. With `uv` installed:

```sh
sudo apt update && sudo apt install -y git tmux
mkdir -p ~/work
git clone https://github.com/Doctacon/gitkraiken.git ~/work/gitkraiken
cd ~/work/gitkraiken
uv tool install --python 3.13 .         # uv supplies Python if necessary
"$(uv tool dir --bin)/gitkraiken" --tmux-config >> ~/.tmux.conf

tmux -V                                # must be 3.2 or newer
# If tmux is already running:
tmux source-file ~/.tmux.conf
# Otherwise start it:
tmux
```

Then `cd` into a repository and press **Ctrl+B, then g**. Prefer keeping checkouts
under WSL's Linux filesystem (for example `~/work`) rather than `/mnt/c` for Git
performance. Do not copy a macOS/Windows `.venv`; install fresh in WSL. Windows
Terminal's WSL profile works well. Authenticate with Git **inside WSL** first;
Windows credentials and SSH agents are not automatically the same as WSL's.

## Private GitHub and Azure DevOps

There is no GitKraiken login, token database, or subscription. **Git operations use
the same credential helper, SSH configuration and agent as command-line Git.**
Use SSH keys or a system credential manager—not a PAT embedded in a URL.
Optional **pull request creation** additionally requires an authenticated GitHub
CLI (`gh auth login`) or Azure CLI with its `azure-devops` extension (`az login`
or `az devops login`). SSH authentication alone cannot create hosted PRs.
See [pull request setup](docs/pull-requests.md); credentials stay with those CLIs.

```sh
# GitHub, including private repositories
gitkraiken --clone git@github.com:ORG/REPO.git ~/work/github-repo

# Azure DevOps SSH
gitkraiken --clone git@ssh.dev.azure.com:v3/ORG/PROJECT/REPO ~/work/azure-repo

# Azure DevOps HTTPS, with your configured Git Credential Manager
gitkraiken --clone https://dev.azure.com/ORG/PROJECT/_git/REPO ~/work/azure-repo
```

Authenticate **once in a normal terminal** first. For GitHub, `gh auth login`
followed by `gh auth setup-git` configures HTTPS authentication if you use the
GitHub CLI. For Azure, use
[Git Credential Manager](https://github.com/git-ecosystem/git-credential-manager)
or [Azure's SSH setup](https://learn.microsoft.com/azure/devops/repos/git/use-ssh-keys).
For GitHub Enterprise or Azure Server, use your organization's regular clone URL;
provider labeling may be generic, but Git transport is not host-restricted.

If authentication fails:

1. Run `git ls-remote <remote-url>` in a normal terminal and complete login or SSH
   host-key acceptance there.
2. Unlock/load the key in your SSH agent as necessary.
3. Retry Fetch/Pull/Push in the app.

Git runs without terminal password prompts, askpass dialogs or interactive SSH
login; network commands time out after 60 seconds. Custom `GIT_SSH_COMMAND`/`GIT_SSH`
wrappers must accept OpenSSH's `-oBatchMode=yes`; shell fragments in custom commands
are normalized rather than executed as arbitrary shell source. Fetch/push URLs
are redacted in the interface. Git itself owns its credential storage/configuration.

## Safety and deliberate limits

- Git is invoked with argument arrays, never interpolated shell commands. File
  paths use literal pathspecs; repository-selection environment variables are
  isolated so operations apply to the repository you opened.
- No force push of history, `git clean`, implicit network activity, or automatic
  stashing. Pull refuses divergence. Safe local branch deletion refuses unmerged
  branches. Discard only affects explicitly selected tracked worktree changes and
  preserves the index; untracked/intent-to-add content is protected by Discard.
  **Hard reset is different:** its explicitly selected mode discards changes and
  may overwrite untracked files. Remote deletion is an explicit destination choice,
  limited to one branch with a last-fetched-SHA lease; local deletion is separate.
- Push sends only the current branch with an explicit non-force refspec, not Git's
  potentially broad configured push defaults. Destination pickers show redacted
  push URLs. Multiple configured push URLs are refused.
- Actions require an explicit selection, but no second confirmation. Git operations
  are serialized; changing selection/refresh cannot cancel a running mutation.
  Repository switching is blocked while a picker or operation is active; context
  actions also reject a changed source branch, HEAD, or target. History-changing
  context actions are not offered during conflicts or unfinished operations.
- This is a **terminal adaptation of the main repository screen**, not an official
  GitKraken product or a pixel-identical clone. No proprietary assets were copied.
  It intentionally omits hosted PR/issue browsing, OAuth/account management,
  arbitrary undo/redo, drag-and-drop rebase, interactive rebase editing, hunk-level
  staging, a graphical conflict editor, and submodule/LFS-specific management.
  Use Git or your editor for those workflows. Git hooks/signing configured by the
  repository still run during commits and can reject or time out an operation.

## Development and checks

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python scripts/tmux_smoke.py  # isolated server; leaves your tmux sessions alone
uv run python scripts/popup_smoke.py # real checkout, live popup/PID, detach/reopen
uv run python scripts/popup_smoke.py --hotkey # prefix+g still detaches explicit popups
uv run python scripts/pane_smoke.py  # pane-local prefix+g, live resize, toggle, quit/crash recovery
uv build
```

Tests use disposable repositories and local bare remotes; they never write to a
real private repository. Textual pilot tests exercise actual staging/committing,
selection, immediate double-click checkout, grouped right-click menus and conflicts,
context-specific creation/reset/upstream/copy actions, worktrees and message editing,
local-time dates, changed-file folders, fold/search persistence, unified graph geometry,
local/remote pointers, diffs, dialogs, branch creation, refresh and narrow layouts.
PR tests cover branch targeting, provider argv/payloads, guarded creation, failure
recovery, and copyable result links using mocked provider commands (no live PRs).
Checkout and notification regressions enable toast rendering explicitly; Textual
otherwise disables notifications in tests. Popup smoke checks exercise real Git
checkouts and verify that the application process and popup stay alive. Pane smoke
checks use a real PTY and three-pane layout to verify containment, native resizing,
independent workspaces, shell/app state preservation, and quit/crash restoration.
Authentication against your organization's private remotes must still be verified
with your credentials; none were requested or accessed during development.

For visual inspection:

```sh
uv run python scripts/capture_demo.py /tmp/workspace.svg --width 170 --height 48
# Add --compact to compare the original denser graph.
# Exercise nested local/remote folders and sidebar-only search:
uv run python scripts/capture_demo.py /tmp/refs.svg --many-refs --ref-filter jlott
# Add --collapse-remotes to preview a folded Remotes section instead.
# Optional: rsvg-convert -o /tmp/workspace.png /tmp/workspace.svg
```

Implementation seams and source research are in [`docs/implementation.md`](docs/implementation.md)
and [`docs/research.md`](docs/research.md).
