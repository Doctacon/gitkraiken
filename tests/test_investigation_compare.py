import pytest

from rhizome.git import GitError, Repository
from rhizome.investigation import InvestigationService


def _commit(repo, git_run, name):
    (repo / name).write_text(f"{name}\n")
    git_run(repo, "add", "--", name)
    git_run(repo, "commit", "-m", name)
    return git_run(repo, "rev-parse", "HEAD").stdout.strip()


def test_comparison_intent_uses_unique_merge_base_or_exact_endpoints(git_repo, git_run):
    base = _commit(git_repo, git_run, "base.txt")
    left = _commit(git_repo, git_run, "left.txt")
    git_run(git_repo, "switch", "-c", "source", base)
    right = _commit(git_repo, git_run, "right.txt")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture((left, right))

    review = service.compare(scope, left, right, "branch-review")

    assert review.intent == "branch-review"
    assert (review.left_oid, review.right_oid) == (left, right)
    assert review.merge_base_oid == base
    assert (review.diff_from_oid, review.diff_to_oid) == (base, right)
    assert [(item.index, item.path) for item in review.files] == [("A", "right.txt")]
    assert "+right.txt" in review.patch
    endpoints = service.compare(scope, left, right, "endpoints")
    assert endpoints.intent == "endpoints"
    assert endpoints.merge_base_oid is None
    assert (endpoints.diff_from_oid, endpoints.diff_to_oid) == (left, right)
    assert [(item.index, item.path) for item in endpoints.files] == [
        ("D", "left.txt"),
        ("A", "right.txt"),
    ]
    assert not endpoints.truncated
    assert endpoints.state.current and endpoints.state.available


def test_branch_review_refuses_criss_cross_multiple_bases(git_repo, git_run):
    base = _commit(git_repo, git_run, "base.txt")
    tree = git_run(git_repo, "rev-parse", f"{base}^{{tree}}").stdout.strip()
    a1 = git_run(git_repo, "commit-tree", tree, "-p", base, "-m", "a1").stdout.strip()
    b1 = git_run(git_repo, "commit-tree", tree, "-p", base, "-m", "b1").stdout.strip()
    a2 = git_run(git_repo, "commit-tree", tree, "-p", a1, "-p", b1, "-m", "a2").stdout.strip()
    b2 = git_run(git_repo, "commit-tree", tree, "-p", b1, "-p", a1, "-m", "b2").stdout.strip()
    assert set(git_run(git_repo, "merge-base", "--all", a2, b2).stdout.split()) == {
        a1,
        b1,
    }
    service = InvestigationService(Repository(git_repo))
    scope = service.capture((a2, b2))

    with pytest.raises(GitError, match="(?i)unique merge base|endpoint"):
        service.compare(scope, a2, b2, "branch-review")
    endpoints = service.compare(scope, a2, b2, "endpoints")
    assert (endpoints.diff_from_oid, endpoints.diff_to_oid) == (a2, b2)
    assert endpoints.merge_base_oid is None


def test_disconnected_branch_review_refuses_but_endpoints_work(git_repo, git_run):
    left = _commit(git_repo, git_run, "left.txt")
    tree = git_run(git_repo, "rev-parse", f"{left}^{{tree}}").stdout.strip()
    right = git_run(git_repo, "commit-tree", tree, "-m", "island").stdout.strip()
    assert git_run(git_repo, "merge-base", "--all", left, right, check=False).returncode == 1
    service = InvestigationService(Repository(git_repo))
    scope = service.capture((left, right))

    with pytest.raises(GitError, match="(?i)unique merge base|endpoint"):
        service.compare(scope, left, right, "branch-review")
    endpoints = service.compare(scope, left, right, "endpoints")
    assert (endpoints.left_oid, endpoints.right_oid) == (left, right)
    assert endpoints.merge_base_oid is None


def test_comparison_rejects_missing_out_of_scope_and_expression_endpoints(git_repo, git_run):
    left = _commit(git_repo, git_run, "left.txt")
    right = _commit(git_repo, git_run, "right.txt")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture((left, right))
    outside = _commit(git_repo, git_run, "outside.txt")

    for invalid in ("f" * 40, outside, "HEAD~1", "refs/heads/main"):
        with pytest.raises(GitError, match="(?i)commit|scope|revision|exact"):
            service.compare(scope, left, invalid, "endpoints")


def test_comparison_remains_exact_and_stale_after_bound_ref_moves(git_repo, git_run):
    base = _commit(git_repo, git_run, "base.txt")
    git_run(git_repo, "switch", "-c", "left", base)
    left = _commit(git_repo, git_run, "left.txt")
    git_run(git_repo, "switch", "-c", "right", base)
    right = _commit(git_repo, git_run, "right.txt")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture(("refs/heads/left", "refs/heads/right"))
    before = service.compare(scope, left, right, "endpoints")
    moved = _commit(git_repo, git_run, "moved.txt")

    after = service.compare(scope, left, right, "endpoints")

    assert moved not in (after.left_oid, after.right_oid)
    assert (after.diff_from_oid, after.diff_to_oid) == (left, right)
    assert after.patch == before.patch
    assert not after.state.current
    assert after.state.available


def test_comparison_marks_large_patch_truncated(git_repo, git_run):
    left = _commit(git_repo, git_run, "base.txt")
    (git_repo / "large.txt").write_text("x" * (2 * 1024 * 1024 + 1024) + "\n")
    git_run(git_repo, "add", "large.txt")
    git_run(git_repo, "commit", "-m", "large")
    right = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture((left, right))

    result = service.compare(scope, left, right, "endpoints")

    assert result.truncated
    assert result.patch.endswith("[diff truncated by Rhizome]\n")
    assert len(result.patch.encode()) < 2 * 1024 * 1024 + 1024
