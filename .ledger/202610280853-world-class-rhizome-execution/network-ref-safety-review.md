## Verdict

There is a material fetch-policy blocker.

Automatic refresh calls the reviewed public path with `Action("fetch")` at `src/rhizome/app.py:648`. Fetch is classified as routine, available, and unconfirmed at `src/rhizome/operations.py:450-451`. Planning hashes effective configuration (`operations.py:665-667, 949-964`) but never decides whether the already-active configuration is safe.

Execution then delegates scope entirely to native Git:

```python
command = ["fetch", "--", remote] if remote else ["fetch", "--all"]
```

at `src/rhizome/git.py:2442-2445`.

Consequently, active fetch refspecs can rewrite local branches, while `fetch.prune` plus `fetch.pruneTags` can delete local tags. Both plans claim only “update remote-tracking refs” (`operations.py:1110`), require no confirmation, create no checkpoint, and report success.

This conflicts with C4’s reviewed/recoverable consequential-mutation rule and transition rule (`world-class-rhizome.md:171-178, 230-233`). C9 authorizes automatic fetching, not destruction of user-owned local refs (`world-class-rhizome.md:335-341`).

## Exact disposable repro

Run from the project root. It uses only temporary repositories, file transport, and public `OperationCoordinator`/`Repository` APIs.

```sh
PYTHONPATH=src python - <<'PY'
import os
import subprocess
import tempfile
from pathlib import Path

from rhizome.git import Repository
from rhizome.models import Action
from rhizome.operations import OperationCoordinator


def git(cwd, *args, check=True):
    return subprocess.run(
        [
            "git",
            "-c", "user.name=Audit User",
            "-c", "user.email=audit@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        env=os.environ,
        check=check,
        text=True,
        capture_output=True,
    )


def oid(repo, ref):
    return git(repo, "rev-parse", ref).stdout.strip()


with tempfile.TemporaryDirectory(prefix="rhizome-fetch-audit-") as raw:
    root = Path(raw)
    home = root / "home"
    xdg = root / "xdg"
    home.mkdir()
    xdg.mkdir()

    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            del os.environ[name]
    os.environ.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ALLOW_PROTOCOL": "file",
    })

    remote = root / "remote.git"
    seed = root / "seed"
    client = root / "client"

    git(root, "init", "--bare", "-b", "main", str(remote))
    git(root, "init", "-b", "incoming", str(seed))
    (seed / "remote.txt").write_text("remote\n")
    git(seed, "add", "remote.txt")
    git(seed, "commit", "-m", "remote")
    incoming = oid(seed, "HEAD")
    git(seed, "push", str(remote), "HEAD:refs/heads/incoming")

    git(root, "init", "-b", "main", str(client))
    (client / "local.txt").write_text("local\n")
    git(client, "add", "local.txt")
    git(client, "commit", "-m", "local")
    git(client, "branch", "victim")
    victim_before = oid(client, "victim")
    git(client, "remote", "add", "origin", str(remote))

    # Gap 1: an already-active forceful fetch destination is a local branch.
    git(
        client, "config", "--replace-all", "remote.origin.fetch",
        "+refs/heads/incoming:refs/heads/victim",
    )

    operations = OperationCoordinator(Repository(client))
    plan = operations.plan(Action("fetch"))
    print("branch plan:", plan.available, plan.requires_confirmation, plan.consequences)
    operations.submit(plan)

    print("branch activity:", operations.activity()[0].state)
    print("victim rewritten:", victim_before != oid(client, "victim"))
    print("victim now remote commit:", oid(client, "victim") == incoming)

    # Gap 2: otherwise-normal tracking config plus pruneTags deletes a private tag.
    git(
        client, "config", "--replace-all", "remote.origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )
    git(client, "tag", "private-local-tag", "main")
    git(client, "config", "fetch.prune", "true")
    git(client, "config", "fetch.pruneTags", "true")

    operations = OperationCoordinator(Repository(client))
    plan = operations.plan(Action("fetch"))
    print("tag plan:", plan.available, plan.requires_confirmation, plan.consequences)
    operations.submit(plan)

    tag = git(
        client, "show-ref", "--verify", "--quiet",
        "refs/tags/private-local-tag", check=False,
    )
    print("tag activity:", operations.activity()[0].state)
    print("private tag deleted:", tag.returncode != 0)
PY
```

Current behavior is:

- Both plans are available and require no confirmation.
- Both consequences mention only remote-tracking refs.
- The first fetch force-moves the un-checked-out `refs/heads/victim`.
- The second fetch deletes `refs/tags/private-local-tag`.
- Both activities report success and have no checkpoint.

The existing tests do not cover active destructive fetch configuration. `tests/test_operations.py:681-714` only proves that a configuration change after planning is detected. Ordinary remote-tracking fetch is covered at `tests/test_operations.py:468-512`.

## Push audit

I found no corresponding push-scope gap.

`src/rhizome/git.py:2539-2555`:

- supplies one explicit `refs/heads/<current>:refs/heads/<destination>` refspec;
- uses no leading `+`;
- supplies `--no-force`, `--no-mirror`, and `--no-follow-tags`;
- overrides `remote.<name>.mirror=false` and `push.followTags=false`;
- therefore does not substitute `push.default` or `remote.<name>.push`.

`tests/test_git.py:1156-1187` exercises real repositories with:

- `push.default=matching`;
- a forceful wildcard `remote.origin.push`;
- `remote.origin.mirror=true`;
- `push.followTags=true`.

Only the selected current branch moves; the other remote branch remains unchanged and the tag is not published. Multiple push URLs are also rejected at `tests/test_git.py:1132-1153`.

## Smallest fail-closed policy

Keep the existing `Action("fetch", remote=...)` interface, but make safe fetch scope authoritative in `Repository`, with planning exposing the same rejection:

1. Read each selected remote’s effective, included fetch refspecs before fetching.
2. Reject malformed or ambiguous positive mappings.
3. Permit destinations only in the remote-tracking cache namespace, `refs/remotes/...`; source-only and negative refspecs are harmless.
4. Reject effective tag pruning and explicit destinations in `refs/heads`, `refs/tags`, `refs/replace`, or other user namespaces.
5. Check existing symbolic destinations recursively. A lexical `refs/remotes/` prefix is insufficient if that symbolic ref resolves to a local branch or tag.
6. Leave remote-tracking rewrites and pruning enabled: these are cache updates authorized by C9, unlike branch/tag destruction.
7. Reject unsafe configuration rather than silently ignoring it and claiming a complete configured fetch. Users can run explicit external Git when they deliberately want those mappings.

Ordinary tag auto-follow is a compatibility edge, not needed to prove this blocker. Git’s normal non-force auto-follow can create absent tags but should not overwrite existing tags. Preserve it only with a real-Git regression proving that property; otherwise `--no-tags` is the stricter alternative.

## Scope limits

- This does not sandbox remote helpers, credential helpers, hooks, filters, or other configured executable tools; C10 explicitly preserves Git’s trust model.
- It does not promise atomicity against arbitrary concurrent same-UID configuration/ref writes.
- Fetch may partially update object storage, `FETCH_HEAD`, or remote-tracking cache before a later remote fails. That is distinct from destroying local branches or tags.
- Push scope cannot constrain arbitrary server-side receive hooks; the audited guarantee is the client’s requested ref update.