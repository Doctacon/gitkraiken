import pytest

from rhizome.git import GitError, Repository
from rhizome.investigation import InvestigationService
from rhizome.models import SearchQuery


def _seed_history(repo, git_run, count=3):
    for index in range(count):
        (repo / "tracked.txt").write_text(f"version {index}\n")
        git_run(repo, "add", "tracked.txt")
        git_run(repo, "commit", "-m", f"version {index}")


def _shallow_clone(source, destination, git_run, depth):
    git_run(
        destination.parent,
        "clone",
        f"--depth={depth}",
        source.as_uri(),
        str(destination),
    )
    return destination


def test_shallow_branch_review_refuses_while_endpoint_comparison_works(git_repo, git_run, tmp_path):
    _seed_history(git_repo, git_run)
    clone = _shallow_clone(git_repo, tmp_path / "shallow", git_run, depth=2)
    right = git_run(clone, "rev-parse", "HEAD").stdout.strip()
    left = git_run(clone, "rev-parse", "HEAD^").stdout.strip()
    service = InvestigationService(Repository(clone))
    scope = service.capture(("HEAD",))

    with pytest.raises(GitError, match="(?i)shallow|endpoint"):
        service.compare(scope, left, right, "branch-review")
    endpoints = service.compare(scope, left, right, "endpoints")

    assert (endpoints.diff_from_oid, endpoints.diff_to_oid) == (left, right)
    assert endpoints.merge_base_oid is None
    assert [(change.index, change.path) for change in endpoints.files] == [("M", "tracked.txt")]
    assert endpoints.state.current and endpoints.state.available
    blame = service.blame(scope, right, "tracked.txt")
    assert "shallow-history" in blame.limitations
    assert blame.complete
    assert blame.state.current


def test_deepened_shallow_boundary_stales_scope_and_refuses_continuations(
    git_repo, git_run, tmp_path
):
    _seed_history(git_repo, git_run)
    clone = _shallow_clone(git_repo, tmp_path / "shallow", git_run, depth=1)
    service = InvestigationService(Repository(clone))
    scope = service.capture()
    head = git_run(clone, "rev-parse", "HEAD").stdout.strip()

    git_run(clone, "fetch", "--deepen=1", "origin", "main")
    state = service.status(scope)

    assert not state.current
    assert state.available
    assert "shallow" in state.reason.lower()
    with pytest.raises(GitError, match="(?i)shallow|boundary|refresh"):
        service.history(scope)
    with pytest.raises(GitError, match="(?i)shallow|boundary|refresh"):
        service.search(scope, SearchQuery(text="version"))
    with pytest.raises(GitError, match="(?i)shallow|boundary|refresh"):
        service.file_history(scope, head, "tracked.txt")
    with pytest.raises(GitError, match="(?i)shallow|boundary|refresh"):
        service.blame(scope, head, "tracked.txt")
