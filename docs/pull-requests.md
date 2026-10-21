# Creating pull requests

## Workflow

1. Check out your feature branch and **Push** its commits first.
2. Right-click the target branch, such as `test` or `origin/test`, in the sidebar
   or on its graph badge. Choose **Start a PR against test from feature/…**.
3. If the remote repository is ambiguous, choose it explicitly. The composer
   shows the actual hosted source → target branch names and repository URL.
4. Enter a **title** (required) and **description** (optional, multiline Markdown).
   Click **Done** or press **Ctrl+Enter** to create the PR.
5. The success screen stays open with the created PR's browser URL and a
   **Copy URL** button. Open that link to manage reviewers, work items, tags,
   policies, and other provider-specific fields. Close returns to the workspace.

The checked-out branch is the source; the right-clicked branch is the target.
There is no inferred Git “parent branch” and selecting a target never checks it out.
Escape or Cancel abandons the form without a request. While creating, submission
and cancellation are disabled to avoid duplicate requests. Failures preserve your
text. A timed-out request may have reached the server: check the repository's PRs
before retrying. GitKraiken never retries creation automatically.

## One-time authentication

Git operations still use your existing SSH agent or Git credential helper. Creating
hosted PRs is a separate API operation: an SSH key alone is not API authentication.
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

Use an account with permission to create PRs in the repository. These tools own
credentials; GitKraiken does not collect or store tokens. The app does not launch
login prompts or automatically install the Azure extension. Missing tools, failed
authentication, or provider rejection are reported in the form. Git authentication
must also work for the branch checks performed on submission.

## Branch and repository rules

- Supports GitHub.com and Azure DevOps Services (`dev.azure.com` and legacy
  `ORG.visualstudio.com`) through supported HTTPS and SSH remote URL shapes.
  GitHub Enterprise, Azure DevOps Server, GitLab, and fork/cross-repository PRs are
  not supported by this composer. Ordinary Git operations are unaffected.
- Source and target must be different branch names in the **same hosted repository**.
  Remote upstreams and custom fetch mappings determine the hosted names. Without
  a remote upstream, a local branch uses its own name on the selected remote.
  Tracking a local parent (`remote = .`) does not make that parent the PR source.
- No automatic push, fetch, merge, checkout, or remote-default-branch guessing.
  On **Done**, an explicit remote read checks that both branches exist and that
  the hosted source is at the captured local HEAD. If not, publish/push first.
  Uncommitted working changes are not part of the PR.
- The target is the **live hosted branch**, which can be newer than its local
  pointer. Local branch/HEAD/target and repository mapping changes while composing
  are rejected; close, refresh, and choose the action again.
- Ambiguous push destinations and push URLs pointing to a different repository are
  refused rather than creating a PR in the wrong repository. Concurrent changes
  by other users after the remote check remain subject to the provider's behavior.

## Implementation and primary sources

The provider adapter in `src/gitkraiken/pull_requests.py` uses argv-based,
noninteractive subprocesses with a 60-second timeout. Tests mock hosted commands;
no real PRs or accounts are touched. Repository preparation and source-tip checks
live in `Repository`; the Textual composer is `pull_request_dialog.py`.

- [GitHub CLI `gh api`](https://cli.github.com/manual/gh_api): authenticated API
  requests; explicit hostname/method; `--input -` accepts a JSON body on stdin.
  GitKraiken supplies explicit repository, `head`, `base`, `title`, and `body`,
  avoiding the implicit branch publishing behavior of higher-level workflows.
- [GitHub create-pull-request API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request):
  creation fields and the returned PR `number`. The result link is the repository's
  `/pull/NUMBER` page, not a compare page.
- [Azure CLI `az repos pr create`](https://learn.microsoft.com/en-us/cli/azure/repos/pr?view=azure-cli-latest#az-repos-pr-create):
  explicit organization, project, repository, source/target, title and description;
  `--detect false` prevents repository auto-detection, and `--open` defaults to false.
  GitKraiken requests JSON and does not request auto-completion or branch deletion.
- [Azure create-pull-request API](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/create?view=azure-devops-rest-7.1):
  the response has `pullRequestId`; `url` is an API resource, not the browser page.
  GitKraiken builds `/pullrequest/ID` from the selected repository and returned ID.
- [Azure CLI implementation](https://github.com/Azure/azure-devops-cli-extension/blob/master/azure-devops/azext_devops/dev/repos/pull_request.py):
  explicit description (including empty) prevents automatic commit-message filling;
  `_open_pull_request` documents the `/PROJECT/_git/REPO/pullrequest/ID` browser route.
- [Azure CLI authentication](https://learn.microsoft.com/en-us/azure/devops/cli/log-in-via-pat?view=azure-devops):
  `az login`, `az devops login`, and CLI-managed PAT authentication options.
