"""Context-menu policy and provider links, independent of widgets and Git processes."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .models import Ref, Remote, Snapshot

MenuActions = list[tuple[str, str] | None]


@dataclass(frozen=True)
class ContextTarget:
    """The exact pointer/commit clicked, never the inspector's previous selection."""

    oid: str
    ref: Ref | None = None

    @property
    def revision(self) -> str:
        return self.ref.full_name if self.ref else self.oid

    @property
    def label(self) -> str:
        return self.ref.name if self.ref else self.oid[:8]


def context_actions(snapshot: Snapshot, target: ContextTarget) -> MenuActions:
    """Only offer operations meaningful for this captured branch/commit and HEAD."""
    ref = target.ref
    current = bool(ref and ref.full_name == f"refs/heads/{snapshot.branch}")
    idle = not snapshot.operation and not snapshot.conflicts
    attached = bool(snapshot.branch and snapshot.head)
    groups: list[list[tuple[str, str]]] = []
    network: list[tuple[str, str]] = []
    if idle and current and snapshot.remotes:
        if snapshot.upstream:
            network.append(("pull", f"Pull {snapshot.branch} (fast-forward only)"))
        network.append(("push", f"Push {snapshot.branch} (never force)"))
    if idle and ref:
        if ref.kind == "local" and any(r.kind == "remote" and r.remote for r in snapshot.refs):
            network.append(("set-upstream", f"Set upstream for {ref.name}…"))
        elif ref.kind == "remote" and ref.remote and attached:
            network.append(("set-upstream", f"Set {snapshot.branch} upstream to {ref.name}"))
    groups.append(network)

    navigation: list[tuple[str, str]] = []
    if idle:
        if not current:
            navigation.append(("checkout", f"Check out {target.label}" + ("…" if not ref else "")))
        navigation.extend(
            [
                ("create-branch", "Create branch here and check out…"),
                ("create-worktree", "Create worktree here…"),
                ("tag", "Create tag here…"),
            ]
        )
    groups.append(navigation)

    history: list[tuple[str, str]] = []
    if idle and attached:
        if not current and target.oid != snapshot.head:
            history.extend(
                [
                    ("merge", f"Merge {target.label} into {snapshot.branch}"),
                    ("rebase", f"Rebase {snapshot.branch} onto {target.label}"),
                    ("cherry-pick", f"Cherry-pick this commit onto {snapshot.branch}"),
                ]
            )
        history.append(("reset", f"Reset {snapshot.branch} to this commit…"))
        if target.oid == snapshot.head:
            history.append(("edit-message", "Edit latest commit message…"))
        history.append(("revert", "Revert this commit"))
    groups.append(history)

    maintenance: list[tuple[str, str]] = []
    if idle:
        maintenance.append(("apply-patch", "Apply patch to working directory…"))
        if ref and ref.kind == "local":
            maintenance.append(("rename-branch", f"Rename {ref.name}…"))
            if not current:
                maintenance.append(("delete-branch", f"Delete {ref.name} (merged only)"))
        elif ref and ref.kind == "remote" and ref.remote:
            maintenance.append(("delete-remote-branch", f"Delete {ref.name} on remote…"))
    groups.append(maintenance)

    copies = []
    if ref:
        copies.append(("copy-branch", "Copy branch name"))
    copies.append(("copy-sha", "Copy commit SHA"))
    linkable = {r.name for r in snapshot.remotes if remote_link(r, "branch", "branch")}
    if ref:
        owner = ref.remote or next(
            (
                r.remote
                for r in snapshot.refs
                if r.kind == "remote"
                and ref.upstream in {r.name, r.full_name, r.full_name.removeprefix("refs/")}
            ),
            "",
        )
        if owner in linkable:
            copies.append(("copy-branch-link", "Copy link to branch on remote"))
    if any(remote_link(remote, "commit", target.oid) for remote in snapshot.remotes):
        copies.append(("copy-commit-link", "Copy link to commit on remote…"))
    groups.append(copies)

    actions: MenuActions = []
    for group in groups:
        if group:
            if actions:
                actions.append(None)
            actions.extend(group)
    return actions


def remote_link(remote: Remote, kind: str, value: str) -> str:
    """Build credential-free links only for supported public provider URL shapes.

    Branch names supplied here are *remote source* names, not tracking aliases.
    Unknown hosts deliberately return no link rather than guessing their routes.
    """
    if kind not in {"branch", "commit"}:
        return ""
    url = remote.web_url or remote.url
    # Azure's SSH URL does not have a browser-equivalent path.
    if url.startswith("https://ssh.dev.azure.com/v3/"):
        parts = url.removeprefix("https://ssh.dev.azure.com/v3/").split("/")
        if len(parts) != 3:
            return ""
        org, project, repo = parts
        url = f"https://dev.azure.com/{org}/{project}/_git/{repo}"
    elif url.startswith("git@ssh.dev.azure.com:v3/"):
        parts = url.removeprefix("git@ssh.dev.azure.com:v3/").split("/")
        if len(parts) != 3:
            return ""
        org, project, repo = parts
        url = f"https://dev.azure.com/{org}/{project}/_git/{repo}"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme == "ssh":
        # SSH ports/usernames belong to Git transport, not the provider website.
        browser = urlunsplit(("https", host, parsed.path, "", ""))
        return remote_link(Remote(remote.name, browser, web_url=browser), kind, value)
    if parsed.scheme not in {"http", "https"} or not host:
        return ""
    base = urlunsplit(
        (parsed.scheme, host + (f":{port}" if port else ""), parsed.path.rstrip("/"), "", "")
    ).removesuffix(".git")
    encoded = quote(value, safe="")
    if host == "github.com":
        return f"{base}/{'tree' if kind == 'branch' else 'commit'}/{encoded}"
    if host == "gitlab.com":
        return f"{base}/-/{'tree' if kind == 'branch' else 'commit'}/{encoded}"
    if host == "dev.azure.com" or host.endswith(".visualstudio.com"):
        if "/_git/" not in parsed.path:
            return ""
        return (
            f"{base}?{urlencode({'version': 'GB' + value})}"
            if kind == "branch"
            else f"{base}/commit/{encoded}"
        )
    return ""
