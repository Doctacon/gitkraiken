import gc
import os
import subprocess
import weakref
from dataclasses import replace

import pytest

from rhizome.git import GitError, Repository
from rhizome.investigation import InvestigationService


def _commit(repo, git_run, filename: str, message: str) -> str:
    (repo / filename).write_text(f"{message}\n")
    git_run(repo, "add", filename)
    git_run(repo, "commit", "-m", message)
    return git_run(repo, "rev-parse", "HEAD").stdout.strip()


def _ref_map(scope) -> dict[str, str]:
    return {captured.full_name: captured.oid for captured in scope.captured_refs}


def _all_history_oids(service, scope, *, order="topology", limit=2) -> list[str]:
    cursor = None
    oids = []
    while True:
        page = service.history(scope, order=order, cursor=cursor, limit=limit)
        oids.extend(commit.oid for commit in page.commits)
        if page.next_cursor is None:
            assert not page.has_more
            return oids
        assert page.has_more
        cursor = page.next_cursor


def _topology_oracle(repo, git_run, scope) -> list[str]:
    return git_run(repo, "rev-list", "--topo-order", *scope.root_oids).stdout.splitlines()


def _git_process(repo, *args, input_text=None, env=None):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
        input=input_text,
        env=env,
    )


def _commit_tree(repo, git_run, tree, message, author_date, committer_date, parent=""):
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args.extend(("-p", parent))
    return _git_process(
        repo,
        *args,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": author_date,
            "GIT_COMMITTER_DATE": committer_date,
        },
    ).stdout.strip()


def test_capture_empty_repository_is_available_unified_scope(git_repo):
    service = InvestigationService(Repository(git_repo))

    scope = service.capture()

    assert scope.kind == "unified"
    assert scope.root_oids == ()
    assert scope.captured_refs == ()
    assert scope.repository_id
    assert scope.scope_id
    assert service.status(scope).current
    assert service.status(scope).available


def test_capture_unified_scope_freezes_all_commit_bearing_refs(git_repo, git_run):
    main_oid = _commit(git_repo, git_run, "main.txt", "main")
    git_run(git_repo, "switch", "-c", "topic")
    topic_oid = _commit(git_repo, git_run, "topic.txt", "topic")
    git_run(git_repo, "tag", "topic-tag")
    git_run(git_repo, "tag", "-a", "annotated-topic", "-m", "annotated topic")
    git_run(git_repo, "switch", "main")

    scope = InvestigationService(Repository(git_repo)).capture()

    assert scope.kind == "unified"
    assert set(scope.root_oids) == {main_oid, topic_oid}
    assert _ref_map(scope) == {
        "refs/heads/main": main_oid,
        "refs/heads/topic": topic_oid,
        "refs/tags/annotated-topic": topic_oid,
        "refs/tags/topic-tag": topic_oid,
    }
    assert scope.head_oid == main_oid
    assert scope.branch == "refs/heads/main"


def test_capture_detached_head_includes_unreferenced_head(git_repo, git_run):
    base_oid = _commit(git_repo, git_run, "base.txt", "base")
    git_run(git_repo, "switch", "-c", "temporary")
    detached_oid = _commit(git_repo, git_run, "detached.txt", "detached")
    git_run(git_repo, "checkout", "--detach", detached_oid)
    git_run(git_repo, "branch", "-D", "temporary")

    scope = InvestigationService(Repository(git_repo)).capture()

    assert scope.kind == "unified"
    assert set(scope.root_oids) == {base_oid, detached_oid}
    assert scope.head_oid == detached_oid
    assert scope.branch == ""
    assert _ref_map(scope) == {"refs/heads/main": base_oid}


def test_unified_history_includes_detached_linked_worktree_heads(git_repo, git_run, tmp_path):
    base = _commit(git_repo, git_run, "base.txt", "base")
    linked = tmp_path / "linked detached"
    git_run(git_repo, "worktree", "add", "--detach", str(linked), base)
    git_run(linked, "commit", "--allow-empty", "-m", "detached linked work")
    tip = git_run(linked, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    assert tip in scope.root_oids
    assert {commit.oid for commit in service.history(scope).commits} == {base, tip}
    git_run(linked, "commit", "--allow-empty", "-m", "move detached linked HEAD")
    assert not service.status(scope).current
    assert service.status(scope).available
    assert {commit.oid for commit in service.history(scope).commits} == {base, tip}


def test_capture_exact_full_ref_binds_its_frozen_oid(git_repo, git_run):
    oid = _commit(git_repo, git_run, "main.txt", "main")
    service = InvestigationService(Repository(git_repo))

    scope = service.capture(root for root in ("refs/heads/main",))

    assert scope.kind == "focused"
    assert scope.root_oids == (oid,)
    assert [(binding.full_name, binding.oid) for binding in scope.bindings] == [
        ("refs/heads/main", oid)
    ]


def test_capture_exact_oid_has_no_moving_ref_binding(git_repo, git_run):
    oid = _commit(git_repo, git_run, "main.txt", "main")
    service = InvestigationService(Repository(git_repo))

    scope = service.capture((oid,))

    assert scope.kind == "focused"
    assert scope.root_oids == (oid,)
    assert scope.bindings == ()


def test_capture_multiple_exact_roots_freezes_both_commits(git_repo, git_run):
    main_oid = _commit(git_repo, git_run, "main.txt", "main")
    git_run(git_repo, "switch", "-c", "topic")
    topic_oid = _commit(git_repo, git_run, "topic.txt", "topic")
    service = InvestigationService(Repository(git_repo))

    scope = service.capture((main_oid, "refs/heads/topic"))

    assert scope.kind == "focused"
    assert scope.root_oids == tuple(sorted((main_oid, topic_oid)))
    assert [(binding.full_name, binding.oid) for binding in scope.bindings] == [
        ("refs/heads/topic", topic_oid)
    ]


@pytest.mark.parametrize("roots", ["HEAD", b"HEAD", None, ("",), (None,), (7,)])
def test_capture_rejects_non_iterable_or_invalid_root_entries(git_repo, roots):
    service = InvestigationService(Repository(git_repo))

    with pytest.raises(ValueError):
        service.capture(roots)


def test_capture_rejects_ambiguous_short_ref(git_repo, git_run):
    _commit(git_repo, git_run, "main.txt", "main")
    service = InvestigationService(Repository(git_repo))

    with pytest.raises(GitError, match="(?i)exact|root|revision|full"):
        service.capture(("main",))


def test_status_reports_relevant_ref_movement_but_keeps_scope_available(git_repo, git_run):
    old_oid = _commit(git_repo, git_run, "old.txt", "old")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture(("refs/heads/main",))

    new_oid = _commit(git_repo, git_run, "new.txt", "new")
    state = service.status(scope)

    assert new_oid != old_oid
    assert not state.current
    assert state.available
    assert state.reason
    assert scope.root_oids == (old_oid,)


def test_status_ignores_unrelated_ref_movement_for_exact_oid_scope(git_repo, git_run):
    oid = _commit(git_repo, git_run, "old.txt", "old")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture((oid,))

    _commit(git_repo, git_run, "new.txt", "new")

    assert service.status(scope).current
    assert service.status(scope).available


def test_status_rejects_foreign_and_tampered_scopes(git_repo, git_run):
    _commit(git_repo, git_run, "main.txt", "main")
    repository = Repository(git_repo)
    owner = InvestigationService(repository)
    foreign = InvestigationService(repository)
    scope = owner.capture()

    foreign_state = foreign.status(scope)
    tampered_state = owner.status(replace(scope, label="altered"))

    assert (foreign_state.current, foreign_state.available) == (False, False)
    assert foreign_state.reason
    assert (tampered_state.current, tampered_state.available) == (False, False)
    assert tampered_state.reason


def test_capture_does_not_retain_discarded_scope(git_repo):
    service = InvestigationService(Repository(git_repo))
    retained = service.capture()
    discarded = service.capture()
    discarded_ref = weakref.ref(discarded)

    del discarded
    gc.collect()

    assert discarded_ref() is None
    assert service.status(retained).available


def test_history_paginates_linear_topology_from_captured_root(git_repo, git_run):
    oldest = _commit(git_repo, git_run, "one.txt", "one")
    middle = _commit(git_repo, git_run, "two.txt", "two")
    newest = _commit(git_repo, git_run, "three.txt", "three")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    first = service.history(scope, limit=2)
    second = service.history(scope, cursor=first.next_cursor, limit=2)

    assert first.scope_id == scope.scope_id
    assert first.order == "topology"
    assert [commit.oid for commit in first.commits] == [newest, middle]
    assert first.has_more
    assert first.next_cursor
    assert first.omitted_before == 0
    assert first.state.current
    assert [commit.oid for commit in second.commits] == [oldest]
    assert not second.has_more
    assert second.next_cursor is None
    assert second.omitted_before == 2


def test_history_cursor_rejects_forgery_wrong_order_and_wrong_scope(git_repo, git_run):
    oldest = _commit(git_repo, git_run, "one.txt", "one")
    _commit(git_repo, git_run, "two.txt", "two")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    cursor = service.history(scope, limit=1).next_cursor
    assert cursor

    payload, signature = cursor.split(".")
    forged_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    with pytest.raises(ValueError, match="cursor"):
        service.history(scope, cursor=f"{forged_payload}.{signature}", limit=1)

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    final_index = alphabet.index(signature[-1])
    assert final_index % 4 == 0
    noncanonical_signature = signature[:-1] + alphabet[final_index + 1]
    with pytest.raises(ValueError, match="cursor"):
        service.history(scope, cursor=f"{payload}.{noncanonical_signature}", limit=1)

    with pytest.raises(ValueError, match="cursor"):
        service.history(scope, order="author-time", cursor=cursor, limit=1)

    other_scope = service.capture((oldest,))
    with pytest.raises(ValueError, match="cursor"):
        service.history(other_scope, cursor=cursor, limit=1)


def test_history_cursor_is_bound_to_originating_service(git_repo, git_run):
    _commit(git_repo, git_run, "one.txt", "one")
    _commit(git_repo, git_run, "two.txt", "two")
    first_service = InvestigationService(Repository(git_repo))
    first_scope = first_service.capture()
    cursor = first_service.history(first_scope, limit=1).next_cursor
    second_service = InvestigationService(Repository(git_repo))
    second_scope = second_service.capture()

    with pytest.raises(ValueError, match="cursor"):
        second_service.history(second_scope, cursor=cursor, limit=1)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1001, 1.0, "2"])
def test_history_rejects_invalid_limits(git_repo, limit):
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(ValueError, match="limit"):
        service.history(scope, limit=limit)


def test_history_rejects_invalid_order(git_repo):
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(ValueError, match="order"):
        service.history(scope, order="date")  # type: ignore[arg-type]


def test_history_uses_frozen_roots_and_reports_moved_ref(git_repo, git_run):
    oldest = _commit(git_repo, git_run, "one.txt", "one")
    captured_head = _commit(git_repo, git_run, "two.txt", "two")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    moved_head = _commit(git_repo, git_run, "three.txt", "three")

    page = service.history(scope, limit=10)

    assert moved_head not in {commit.oid for commit in page.commits}
    assert [commit.oid for commit in page.commits] == [captured_head, oldest]
    assert page.commits[0].refs == ("main", "HEAD")
    assert not page.state.current
    assert page.state.available
    assert page.state.reason


def test_history_rejects_foreign_scope_with_safe_git_error(git_repo, git_run):
    _commit(git_repo, git_run, "one.txt", "one")
    owner = InvestigationService(Repository(git_repo))
    foreign = InvestigationService(Repository(git_repo))
    scope = owner.capture()

    with pytest.raises(GitError, match="unavailable"):
        foreign.history(scope)


def test_history_merge_topology_and_parents_match_git(git_repo, git_run):
    _commit(git_repo, git_run, "base.txt", "base")
    git_run(git_repo, "switch", "-c", "side")
    _commit(git_repo, git_run, "side.txt", "side")
    git_run(git_repo, "switch", "main")
    _commit(git_repo, git_run, "main.txt", "main")
    git_run(git_repo, "merge", "--no-ff", "side", "-m", "merge side")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    pages = _all_history_oids(service, scope, limit=2)
    oracle = _topology_oracle(git_repo, git_run, scope)
    page = service.history(scope, limit=100)

    assert pages == oracle
    assert {commit.oid: commit.parents for commit in page.commits} == {
        oid: tuple(git_run(git_repo, "show", "-s", "--format=%P", oid).stdout.split())
        for oid in oracle
    }


def test_history_octopus_topology_and_parents_match_git(git_repo, git_run):
    _commit(git_repo, git_run, "base.txt", "base")
    for branch in ("red", "green", "blue"):
        git_run(git_repo, "switch", "-c", branch, "main")
        _commit(git_repo, git_run, f"{branch}.txt", branch)
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "merge", "--no-ff", "red", "green", "blue", "-m", "octopus")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    page = service.history(scope, limit=100)
    oracle = _topology_oracle(git_repo, git_run, scope)
    head = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    oracle_parents = tuple(git_run(git_repo, "show", "-s", "--format=%P", head).stdout.split())

    assert _all_history_oids(service, scope, limit=2) == oracle
    assert next(commit for commit in page.commits if commit.oid == head).parents == oracle_parents
    assert len(oracle_parents) == 4


def test_history_disconnected_roots_match_git_topology(git_repo, git_run):
    _commit(git_repo, git_run, "one.txt", "one")
    _commit(git_repo, git_run, "two.txt", "two")
    tree = git_run(git_repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    island = git_run(git_repo, "commit-tree", tree, "-m", "island").stdout.strip()
    git_run(git_repo, "update-ref", "refs/heads/island", island)
    service = InvestigationService(Repository(git_repo))
    scope = service.capture(("refs/heads/main", "refs/heads/island"))

    page = service.history(scope, limit=100)

    assert _all_history_oids(service, scope, limit=1) == _topology_oracle(git_repo, git_run, scope)
    assert next(commit for commit in page.commits if commit.oid == island).parents == ()


def test_author_time_is_global_across_tiny_cursor_pages(git_repo, git_run):
    tree = _git_process(git_repo, "mktree", input_text="").stdout.strip()
    a1 = _commit_tree(
        git_repo, git_run, tree, "a1", "2001-01-01T00:00:00+0000", "2030-01-01T00:00:00+0000"
    )
    a2 = _commit_tree(
        git_repo, git_run, tree, "a2", "2024-01-01T00:00:00+0000", "2002-01-01T00:00:00+0000", a1
    )
    b1 = _commit_tree(
        git_repo, git_run, tree, "b1", "2023-01-01T00:00:00+0000", "2040-01-01T00:00:00+0000"
    )
    b2 = _commit_tree(
        git_repo, git_run, tree, "b2", "2025-01-01T00:00:00+0000", "2003-01-01T00:00:00+0000", b1
    )
    git_run(git_repo, "update-ref", "refs/heads/main", a2)
    git_run(git_repo, "update-ref", "refs/heads/other", b2)
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    reachable = git_run(git_repo, "rev-list", "--all").stdout.splitlines()
    expected = sorted(
        reachable,
        key=lambda oid: int(git_run(git_repo, "show", "-s", "--format=%at", oid).stdout.strip()),
        reverse=True,
    )

    assert _all_history_oids(service, scope, order="author-time", limit=1) == expected


def test_author_time_ties_are_deterministic_across_pages(git_repo, git_run):
    tree = _git_process(git_repo, "mktree", input_text="").stdout.strip()
    same_date = "2020-01-01T00:00:00+0000"
    left1 = _commit_tree(git_repo, git_run, tree, "left1", same_date, "2021-01-01T00:00:00+0000")
    left2 = _commit_tree(
        git_repo, git_run, tree, "left2", same_date, "2022-01-01T00:00:00+0000", left1
    )
    right1 = _commit_tree(git_repo, git_run, tree, "right1", same_date, "2023-01-01T00:00:00+0000")
    right2 = _commit_tree(
        git_repo, git_run, tree, "right2", same_date, "2024-01-01T00:00:00+0000", right1
    )
    git_run(git_repo, "update-ref", "refs/heads/main", left2)
    git_run(git_repo, "update-ref", "refs/heads/right", right2)
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    oracle = git_run(
        git_repo,
        "log",
        "--author-date-order",
        "--format=%H",
        *scope.root_oids,
        "--",
    ).stdout.splitlines()

    first = _all_history_oids(service, scope, order="author-time", limit=1)
    second = _all_history_oids(service, scope, order="author-time", limit=2)

    assert first == oracle
    assert second == oracle


def test_history_pages_beyond_five_thousand_commits(git_repo, git_run):
    commit_count = 5001
    stream = []
    previous_mark = 0
    for index in range(commit_count):
        mark = index + 1
        subject = f"commit {index}"
        stream.extend(
            (
                "commit refs/heads/main\n",
                f"mark :{mark}\n",
                f"committer Test User <test@example.invalid> {946684800 + index} +0000\n",
                f"data {len(subject)}\n{subject}\n",
            )
        )
        if previous_mark:
            stream.append(f"from :{previous_mark}\n")
        stream.append("\n")
        previous_mark = mark
    stream.append("done\n")
    _git_process(git_repo, "fast-import", "--quiet", input_text="".join(stream))
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    oracle = git_run(git_repo, "rev-list", "--topo-order", "HEAD").stdout.splitlines()

    actual = _all_history_oids(service, scope, limit=997)

    assert len(actual) == commit_count
    assert actual == oracle


def test_status_reports_pruned_captured_root_unavailable(git_repo, git_run):
    _commit(git_repo, git_run, "base.txt", "base")
    git_run(git_repo, "switch", "-c", "temporary")
    root = _commit(git_repo, git_run, "temporary.txt", "temporary")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture((root,))
    git_run(git_repo, "switch", "main")
    git_run(git_repo, "branch", "-D", "temporary")
    git_run(git_repo, "reflog", "expire", "--expire=now", "--all")
    git_run(git_repo, "gc", "--prune=now")

    state = service.status(scope)

    assert git_run(git_repo, "cat-file", "-e", root, check=False).returncode != 0
    assert not state.current
    assert not state.available
    assert state.reason
    with pytest.raises(GitError, match="available|object"):
        service.history(scope)


def test_status_reports_repository_replacement_unavailable(git_repo, git_run):
    _commit(git_repo, git_run, "base.txt", "base")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    (git_repo / ".git").rename(git_repo.parent / "replaced.git")
    git_run(git_repo, "init", "-b", "main")

    state = service.status(scope)

    assert not state.current
    assert not state.available
    assert state.reason
    with pytest.raises(GitError, match="available|replaced"):
        service.history(scope)
