# Rhizome usage guide

[Quickstart](../README.md) · [Pinned installation](installation.md)

Rhizome is a local-first, terminal-native Git workspace. This development checkout
contains the precision-core upgrade in progress, not a certified release of the
entire [roadmap](design/world-class-rhizome.md). Later investigation, integration,
personalization, and captured hosted-review workflows remain incomplete.
Ordinary staged commits are available through a reviewed-index transaction with
native hooks/signing and a deliberate terminal handoff. Release and cross-platform
acceptance remain outstanding; the installer crash/retry defect is still deferred.

## Run

Use Git 2.36+, Python 3.13+, and uv for a development checkout:

```sh
uv sync --locked
uv run --locked rhizome /path/to/repository
uv run --locked rhizome --demo
# Explicitly clone into a new or empty destination, then open:
uv run --locked rhizome --clone git@github.com:ORG/REPO.git ~/work/repo
```

A path inside a worktree is sufficient; bare repositories are rejected. The demo
uses a disposable real Git repository and a local-only remote. No external service
is cloned or pushed by the demo. `uv run --locked python -m rhizome [path]` also works.

The target environment is macOS/Linux/WSL2, including SSH and optional tmux 3.2+.
The precision flow has headless **80×24** coverage; that is not proof of every
terminal, platform, minimum version, assistive technology, or human usability gate.
At wide sizes all three panels fit. In a constrained terminal, Files, Commit, Refs,
and Back summon one primary task rather than squeezing all panels together.

## Browse without checking out

- **References:** the searchable tree groups repository, local branches, remotes,
  worktrees, PRs, tags and stashes. Carets and Space fold groups. Selecting or
  double-clicking a branch sets context; it never checks out or filters history.
- **History:** select or double-click a commit to inspect it. Right-click its row
  for exact commit actions, or its branch-name badge for branch-specific actions.
- **Details:** activate a changed file to open its diff. Back/Esc restores the
  graph; merely selecting a commit does not open a file diff.
- **Worktrees:** opening a registered worktree is navigation to another checkout,
  not a checkout operation in the current tree. New worktree creation leaves the
  current checkout intact and asks for an explicit branch/detached choice and path.

Only an explicit **Checkout** command changes the checkout. Remote checkout uses
exact remote-ref identity: an unrelated same-named local branch is not silently
substituted. A ref/HEAD/context change while a prompt is open invalidates the action.

Current history still uses the existing author-time timeline, loaded in batches
of 300 up to 5,000. Search currently covers that loaded history. Complete captured-
scope search, topology-first ordering, bounded deep pagination, explicit focus,
comparisons, file history, and blame belong to M2 and are **not yet implemented**.
Selecting a reference does not pretend to provide those features.

### Graph and pointer details

Smooth and Compact change rendering density, not commit ordering. Press **g** or
use the graph-style button; the preference now survives restart. Dates use local
time, including daylight-saving rules. Connector-only rows and the WIP row have
no invented author/date. WIP appears only when there are changes.

The graph shows local branch/HEAD, linked worktree, provider/remote, tag, and stash
pointers. Several pointers can share a commit without inventing a fork. Registered
linked-worktree markers are independent of branch names and include detached
worktrees. Remote-tracking pointers mean **last fetched**, not live server state.

Crossings are not joins. Dotted tails continue beyond loaded history. If author
date skew places a parent above its child, an upward parent-SHA annotation names
it rather than inventing a downward edge. Graph lanes are not cropped away.

Drag pane dividers or column-header grips to resize. Session widths, folds and
scroll positions survive ordinary refresh. Narrowing limits visible panes;
expanding restores explicit side-panel preferences. Hover popup tooltips are
suppressed in the workspace.

## Craft a precise commit

1. Choose **Files / F2**. Staged and unstaged versions of a mixed file have separate
   entries. A row highlight and an explicitly opened review are independent.
2. Activate the file to open the canonical unified diff. The diff takes keyboard
   focus; return focus to Files when changing the whole-file selection.
3. Press **v** to anchor a contiguous range, extend it with arrows, then **s** to
   stage or **u** to unstage. Mouse click/Shift-click select ranges. Hunk controls
   operate on the current hunk. File and all-file staging remain available.
4. Search, expand context, show whitespace and inspect word emphasis as reading
   aids. These controls never reconstruct the Git patch from displayed text.
5. Choose **Commit / F3** and write a summary and optional body. The composer shows
   staged-file scope. Choose **Commit** to run native Git against the reviewed index.
   The workspace temporarily hands over the terminal so hooks/signing can prompt,
   then resumes. A rejected commit retains your exact draft; success clears it.

When a repository opens without a nonempty saved draft, the composer loads its
configured `commit.template`. Saved drafts always win, including comments or
whitespace. An untouched template is not saved as a draft and must be edited before
submission. Missing, unreadable, nonregular, oversized (over 1 MiB), undecodable,
or unsupported-line-ending templates show an error; write your own message or fix
the template and reopen. After a successful commit the composer stays cleared;
reopening can seed the next template.

Accepted text is passed without app-side trimming through stdin, using Git's
**editor-mode** cleanup (`--edit --no-status --no-verbose -F -`). With default cleanup,
Git removes comment lines; `commit.cleanup` and `core.commentChar` remain authoritative.
Status/diff text is not appended behind the composer's back, and no second editor
opens. Message hooks can rewrite or reject the text; `prepare-commit-msg` receives
source `message`. Templates and submissions use `i18n.commitEncoding` (UTF-8 by
default); unsupported encodings or unrepresentable characters fail visibly and
retain the draft. Summary/body guidance is optional, not a required message schema.

If Git's outcome or index publication is uncertain, inspect the reported HEAD/index
and retained transaction files before retrying. Rhizome never automatically retries
or kills a running commit.

Selected staging/unstaging changes the index, not the working file. Unsupported
representations, conversions, binary/oversized data and stale input fail explicitly;
Rhizome does not approximate a selected patch or silently fall back to whole-file
staging. Large static commit previews identify truncation.

### Changed input stays visible

An external edit or Git operation does not silently replace a captured precision
review. It retains text, selection/cursor and scroll, marks the view **stale**, and
refuses application. Use the diff's explicit Refresh, inspect the new content and
select again. A successful action initiated in the app may deliberately show its
new result. Refreshing summary data is not permission to retarget a selection.

## Drafts and core preferences

Commit drafts are private, worktree-specific state outside the checkout. Linked
worktrees do not share a draft. Reopening restores text; completing a commit or
explicitly clearing the editor clears it. A failed write is reported as **not
saved**, rather than claiming durability.

PR title/body drafts are additionally keyed by hosted provider/repository and
source/target branch intent. Closing the composer retains them; another target
gets its own draft. A moving source commit does not erase the draft. Successful
creation clears that intent's saved text; a cleanup failure does not report the
already-created PR as a failed creation.

The state location and installation are [separate](installation.md#locations-and-ownership).
`settings.json` is a versioned editable file in that state location. Existing core
preferences include `graph_style`, `offline`, `remote_refresh_enabled`,
`remote_refresh_interval`, and `protected_branches`. For example:

```json
{
  "schema_version": 1,
  "data": {
    "graph_style": "smooth",
    "offline": false,
    "remote_refresh_enabled": true,
    "remote_refresh_interval": 60,
    "protected_branches": ["release"]
  }
}
```

Restart after manual edits. Invalid settings surface an error and pause automatic
network activity rather than silently enabling defaults. Full settings/keymap UI,
adaptive themes and the broader persistence controls remain M2 work; the storage
schema alone is not evidence that every preference is wired into the app.

## Safety during the implementation transition

Routine supported actions currently include stage/unstage, ordinary commits, explicit
checkout, branch/tag/worktree creation, upstream selection, validated fetch and
non-force push. Fetch evaluates active refspecs, pruning settings and symbolic
aliases before transport. It refuses configured refspec destinations outside remote-tracking caches,
tag pruning, unsupported explicit tag modes, and updates that could move an
existing user-owned symbolic ref through a cache. Native non-force tag auto-follow
and explicit `--no-tags` remain supported; configured pruning may remove obsolete
cached refs. As in native Git, a previously dangling symbolic alias may become
resolvable when a new cache ref or auto-followed tag arrives; that does not discard
an existing tip. Use deliberate external Git for refused configurations.

Plans capture relevant identity, targets, configuration and content, then
revalidate under common-repository mutation serialization. They cannot be replayed
or silently retargeted.

The current workspace deliberately refuses **discard, resets, history rewrites,
merge/rebase/cherry-pick/revert, stash mutations, pull, ref deletion, external-patch
application, Continue and Abort** while their guided protection/recovery flows
remain unproven. Some existing menus/prompts remain visible, but submission explains
the gate and does not execute Git. Selecting a hard-reset mode is not an exception.
Protected/default refs cannot bypass these guards.

For a gated workflow, inspect `git status` and use Git or your editor explicitly in
another terminal, then refresh Rhizome. An externally started conflict remains
visible; finish it externally for now. **Rhizome does not checkpoint or promise
recovery for external actions.** The underlying recovery component is not yet a
complete user-facing recovery workflow. There is no universal Undo.

Push uses an explicit non-force refspec and destination; broad configured push
defaults are not substituted. Multiple push destinations are refused. Transport
failure can leave a remote outcome uncertain: inspect it before deciding to retry.
No automatic mutation retry, force-push, `git clean`, or automatic stashing is added.
Repository hooks/signing still run and can reject an operation. Ordinary commit
handoffs have no overall timeout or automatic cancellation.

## Refresh and activity

Local polling runs on a two-second cadence when the workspace permits it. Optional
automatic fetch and hosted metadata use a configurable **60-second** interval,
back off after failures, and pause when the terminal is inactive or user work
prevents a new request. Offline mode pauses automatic network activity. Automatic
fetch uses the same destination, pruning, and alias validation as manual fetch.

Slow automatic transport no longer owns the local inspection queue. Git mutations
still serialize, so an already-running fetch can delay another mutation; it is not
killed to make room. Captured reviews remain stable during summary refresh.

**Activity / Ctrl+L** shows queued/planning, running and bounded completed Git work,
elapsed time, redacted result detail, and checkpoint IDs when applicable. Background
read status is separate. Keyboard arrows or mouse selection inspect a record.
**Close/Escape only closes Activity—it never cancels or retries Git.**

## Controls

| Key/action | Result |
| --- | --- |
| Click / arrows / `j`, `k` | Select history or files |
| Sidebar click / Enter / double-click | Select a reference; never checkout |
| Graph double-click | Inspect; never checkout |
| Right-click reference/badge/commit | Exact context actions |
| Caret / Space | Fold references or changed-file directories |
| `/` or `f`; Enter / Next | Search loaded history; next match |
| `g` | Smooth / Compact graph |
| `F2`; `F3` | Files; Commit composer |
| `v`, arrows, `s` / `u` in precision diff | Select range; stage / unstage |
| `Esc` / Back | Return from review; clear selection/search; dismiss a dialog |
| `Ctrl+P` / `x` | Context actions |
| `Ctrl+L` / Activity | Read-only activity view |
| `Ctrl+O` | Open a repository |
| `Ctrl+R` / `r` | Refresh local state |
| `Ctrl+B` | Explicit new-branch action |
| `Ctrl+Left` / `Ctrl+Right` | Refs / inspector visibility |
| `?`; `Ctrl+Q` | Help; quit |

Ordinary letters do not steal typing from search or composers. Explicit global
Ctrl/function-key commands remain available. Refer to the current visible controls;
full remapping and broader accessibility validation remain unfinished.

## tmux

Standalone operation is the default. tmux **3.2+** adds a persistent repository
window, pane-local toggle, or explicit popup without requiring a plugin.

```sh
rhizome --tmux ~/work/repo
rhizome --popup ~/work/repo
# Print a proposed binding; this command does not edit configuration:
rhizome --tmux-config
```

Review the generated binding before choosing to add it to `~/.tmux.conf` or source
it in your server. It overrides **prefix + g** if you load it. No installer or app
command silently changes your bindings. Generate it on the machine/environment
where it will run; do not copy a macOS binding into WSL.

With that opt-in binding, **prefix + g** opens/resumes Rhizome in the invoking pane's
space and toggles back without losing the original shell/editor. Other panes stay
visible. Each originating pane/repository pair has its own workspace. Outside a
repository, the invoking client receives an error. Custom sockets are supported.

The inactive pane is parked in a detached `rhizome-pane-…` session. Do not kill it
while it contains your shell/editor. **Ctrl+Q** exits Rhizome and restores the pane;
process-crash restoration is also exercised by the smoke script. Destructive tmux
`kill-pane`/`kill-window` commands are not a substitute for this toggle. If the
original process exits while parked, it cannot be restored.

In an explicit popup, normal **prefix + d** or the opt-in **prefix + g** detaches the
popup; reopening resumes it. In pane-local mode, normal prefix + d still detaches
your entire tmux client. **Ctrl+Q** exits the application instead of detaching it.

Generated bindings contain an absolute Python path. Regenerate after moving a
development environment or selecting a different managed installation revision.
For development, use `uv run --locked rhizome --tmux-config`. When migrating from
`gitkraiken`, replace its old generated binding; module and executable names changed.

For WSL2, run inside the Linux shell and install dependencies there. Prefer the
Linux filesystem (for example `~/work`) over `/mnt/c` for Git workloads. Authenticate
inside WSL; Windows credentials/agents are not automatically the same environment.

## Authentication and hosted metadata

There is no Rhizome login, token database, subscription or telemetry. Normal Git
operations use your existing credential helper, SSH configuration and agent. Use
SSH keys or a credential manager, not tokens embedded in remote URLs. Hosted PR
listing/creation additionally uses optional authenticated `gh` or `az` CLIs; SSH
alone is not hosted API authentication. See [pull requests](pull-requests.md).

Authenticate in a normal terminal first. On failure, inspect `git ls-remote` there,
complete login/host-key acceptance or unlock your SSH agent, then retry the intended
app operation. Network Git commands have a 60-second timeout and do not prompt for
passwords or launch askpass dialogs. Custom SSH wrappers must accept OpenSSH's
`-oBatchMode=yes`. Git paths are literal argv, not interpolated shell source;
repository-selection environment overrides are isolated. Displayed remote URLs
are redacted.

Generic Git transport can use organization-hosted servers; the optional hosted
metadata feature targets GitHub.com and Azure DevOps Services. Captured immutable
PR diffs/review workspaces are M4 work, not supplied by the existing browser-link
metadata dialog. PR creation uses pushed branches, never implicitly pushes or
merges, and leaves reviewers/comments/merging to the provider UI.

## Clipboard support

Copy actions use `pbcopy` on macOS, `wl-copy` on Wayland, `xclip`/`xsel` on X11, or
WSL's `clip.exe` when available. Missing/failing helpers fall back to OSC 52. SSH
uses OSC 52 rather than changing the remote machine's clipboard. Terminal requests
cannot be confirmed: **paste to verify**. Only a successful native helper reports
system-clipboard success. A created PR URL also stays visible for browser copying.

## Development and checks

```sh
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python scripts/tmux_smoke.py
uv run --locked python scripts/popup_smoke.py
uv run --locked python scripts/popup_smoke.py --hotkey
uv run --locked python scripts/pane_smoke.py
uv build
```

Tests isolate state, use disposable repositories/local remotes, and prevent real
private-provider mutations. New safety proofs use real Git with mocks only at
external boundaries. Existing legacy tests are not all rewritten to that standard.
Headless Pilot tests do not certify screen-reader behavior or human usability.
Smoke scripts use isolated tmux servers; run them only in disposable fixtures.

```sh
uv run --locked python scripts/capture_demo.py /tmp/workspace.svg --width 170 --height 48
# Optional conversion when librsvg is installed:
rsvg-convert -o /tmp/workspace.png /tmp/workspace.svg
```

The complete changed suite and the external/platform/human gates must pass before
claiming a milestone complete. See the [validation plan](design/validation-plan.md),
[implementation notes](implementation.md), and [source research](research.md).
