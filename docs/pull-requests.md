# Browsing and creating pull requests

## Browse open pull requests

Open pull requests load automatically in **PRs** in the left sidebar, below Worktrees.
The app contacts each supported GitHub.com or Azure DevOps fetch repository using
the CLI authentication below. Multiple remotes for the same hosted repository are listed only once.
Closed, merged, completed, and abandoned PRs are excluded; open drafts are included.
Results include the PR number, title, author, draft state, and source/target branches.
Filter by any of these fields, then click a PR or press Enter for its read-only
summary and **Open in browser** / **Copy URL** actions. Reviewing and merging remain
in the provider's web UI; selecting a PR never checks it out.

Lists refresh on opening/resuming the workspace and on the configurable 60-second
automatic cadence, while active and network access is enabled. Offline settings
pause automatic requests. Use **Refresh PRs** to reload sooner when network access
is enabled. Existing results stay visible during automatic
refreshes; expanding/collapsing sections does not trigger extra requests. Lists are
session-local, reset when the repository or remotes change, and refreshed after
creating a PR. Each remote has its own
empty/error state, so one unavailable provider does not hide successful results.
Unsupported remotes show a setup explanation rather than an empty-looking success.
Provider listings are paginated; oversized or malformed responses produce an explicit
error rather than silently presenting a partial list.

## Creation workflow

1. Check out your feature branch and **Push** its commits first.
2. Right-click the target branch, such as `test` or `origin/test`, in the sidebar
   or on its graph badge. Choose **Start a PR against test from feature/…**.
3. If the remote repository is ambiguous, choose it explicitly. The composer
   shows the actual hosted source → target branch names and repository URL.
4. Enter a **title** (required) and **description** (optional, multiline Markdown).
   Click **Done** or press **Ctrl+Enter** to create the PR.
5. The success screen stays open with the created PR's browser URL and a
   **Copy URL** button. Open that link to manage reviewers, work items, tags,
   policies, and other provider-specific fields. Copy uses the system clipboard
   when available; local copying does not require terminal OSC 52 support. If the
   app falls back to a terminal copy request, paste to verify or open the link and
   copy it from your browser's address bar. See [clipboard support](usage.md#clipboard-support).
   Close returns to the workspace.

The checked-out branch is the source; the right-clicked branch is the target.
There is no inferred Git “parent branch” and selecting a target never checks it out.
Escape or Cancel closes the form without a request and retains its private local
draft. Title and body survive reopening, separately for each worktree and hosted
provider/repository/source/target intent. Another target never inherits this text.
Successful creation clears that saved intent; persistence failures are shown rather
than reported as saved. Offline mode still allows composing and saving a draft, but
refuses publication before any branch verification or provider request. While creating, submission and cancellation are disabled
to avoid duplicate requests. Failures preserve your text. A timed-out request may have reached the server: check the repository's PRs
before retrying. Rhizome never retries creation automatically.

## One-time authentication

Git operations still use your existing SSH agent or Git credential helper. Browsing
and creating hosted PRs use separate API operations: an SSH key alone is not API authentication.
Install and authenticate the appropriate optional CLI in a normal terminal:

```sh
# GitHub.com — install GitHub CLI, then:
gh auth login --hostname github.com

# Azure DevOps Services — install Azure CLI, then:
az extension add --name azure-devops
az login
# Alternatively, use a PAT through Azure DevOps CLI's own login:
az devops login --organization https://dev.azure.com/YOUR_ORG
```

Use an account with repository read access for browsing, and permission to create PRs
for the composer. These tools own
credentials; Rhizome does not collect or store tokens. The app does not launch
login prompts or automatically install the Azure extension. Missing tools, failed
authentication, or provider rejection are reported in the PR list or composer. Git
authentication must also work for the branch checks performed on submission.

## Branch and repository rules

- Supports GitHub.com and Azure DevOps Services (`dev.azure.com` and legacy
  `ORG.visualstudio.com`) through supported HTTPS and SSH remote URL shapes.
  GitHub Enterprise, Azure DevOps Server, GitLab, and fork/cross-repository PRs are
  not supported by this composer. Ordinary Git operations are unaffected.
- Source and target must be different branch names in the **same hosted repository**.
  Remote upstreams and custom fetch mappings determine the hosted names. Without
  a remote upstream, a local branch uses its own name on the selected remote.
  Tracking a local parent (`remote = .`) does not make that parent the PR source.
- The composer never pushes, fetches, merges, checks out, or guesses a remote default branch.
  On **Done**, an explicit remote read checks that both branches exist and that
  the hosted source is at the captured local HEAD. If not, publish/push first.
  Uncommitted working changes are not part of the PR.
- The target is the **live hosted branch**, which can be newer than its local
  pointer. Local branch/HEAD/target and repository mapping changes while composing
  are rejected; close, refresh, and choose the action again.
- Ambiguous push destinations and push URLs pointing to a different repository are
  refused rather than creating a PR in the wrong repository. Concurrent changes
  by other users after the remote check remain subject to the provider's behavior.

Captured immutable PR diffs/review workspaces are a later M4 feature. The current
metadata/browser dialog does not provide that review guarantee. No live private-
provider certification or real PR creation is implied by the local test suite.

## Implementation and primary sources

The provider adapter in `src/rhizome/pull_requests.py` uses argv-based,
noninteractive subprocesses with a 60-second timeout. Tests mock hosted commands;
no real PRs or accounts are touched. Repository preparation and source-tip checks
live in `Repository`; the Textual composer and read-only summary are in
`pull_request_dialog.py`. Open-list clients use `gh api` (GET, `state=open`) and
`az repos pr list` (`--status=active`), with explicit repository identities and
pagination. Browser URLs are built from those identities and validated positive
PR numbers, never trusted from provider response URLs.

- [GitHub list-pull-requests API](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests):
  open-state filtering, pagination, and source/base branch metadata.
- [Azure CLI `az repos pr list`](https://learn.microsoft.com/en-us/cli/azure/repos/pr?view=azure-cli-latest#az-repos-pr-list):
  active-state filtering, explicit organization/project/repository, and `--skip` / `--top` pagination.

- [GitHub CLI `gh api`](https://cli.github.com/manual/gh_api): authenticated API
  requests; explicit hostname/method; `--input -` accepts a JSON body on stdin.
  Rhizome supplies explicit repository, `head`, `base`, `title`, and `body`,
  avoiding the implicit branch publishing behavior of higher-level workflows.
- [GitHub create-pull-request API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request):
  creation fields and the returned PR `number`. The result link is the repository's
  `/pull/NUMBER` page, not a compare page.
- [Azure CLI `az repos pr create`](https://learn.microsoft.com/en-us/cli/azure/repos/pr?view=azure-cli-latest#az-repos-pr-create):
  explicit organization, project, repository, source/target, title and description;
  `--detect false` prevents repository auto-detection, and `--open` defaults to false.
  Rhizome requests JSON and does not request auto-completion or branch deletion.
- [Azure create-pull-request API](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/create?view=azure-devops-rest-7.1):
  the response has `pullRequestId`; `url` is an API resource, not the browser page.
  Rhizome builds `/pullrequest/ID` from the selected repository and returned ID.
- [Azure CLI implementation](https://github.com/Azure/azure-devops-cli-extension/blob/master/azure-devops/azext_devops/dev/repos/pull_request.py):
  explicit description (including empty) prevents automatic commit-message filling;
  `_open_pull_request` documents the `/PROJECT/_git/REPO/pullrequest/ID` browser route.
- [Azure CLI authentication](https://learn.microsoft.com/en-us/azure/devops/cli/log-in-via-pat?view=azure-devops):
  `az login`, `az devops login`, and CLI-managed PAT authentication options.
