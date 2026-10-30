from datetime import UTC, datetime

import pytest

from rhizome.git import GitError, Repository
from rhizome.investigation import InvestigationService
from rhizome.models import SearchQuery


def test_search_examines_complete_scope_in_bounded_batches_and_jumps(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "old subject\n\nHidden NEEDLE in the body")
    old = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "commit", "--allow-empty", "-m", "new subject")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    query = SearchQuery(text="needle")

    first = service.search(scope, query, scan_limit=1)

    assert first.hits == ()
    assert first.examined == first.examined_total == 1
    assert not first.complete
    assert first.next_cursor
    last = service.search(scope, query, cursor=first.next_cursor, scan_limit=1)
    assert last.complete
    assert last.next_cursor is None
    assert last.examined == 1
    assert last.examined_total == 2
    assert [hit.commit.oid for hit in last.hits] == [old]
    assert "message" in last.hits[0].matched_fields
    jumped = service.history(scope, cursor=last.hits[0].history_cursor, limit=1)
    assert jumped.omitted_before == 1
    assert [commit.oid for commit in jumped.commits] == [old]


def test_search_ands_literal_rename_path_and_inclusive_author_dates(git_repo, git_run, monkeypatch):
    original, renamed = ":(glob)*[old].txt", "new?[file].txt"
    (git_repo / original).write_text("unchanged through rename\n")
    git_run(git_repo, "--literal-pathspecs", "add", "--", original)
    git_run(git_repo, "commit", "-m", "base")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2025-01-02T01:00:00+01:00")
    git_run(git_repo, "mv", "--", original, renamed)
    git_run(git_repo, "commit", "-m", "criterion rename")
    wanted = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2025-01-03T00:00:00Z")
    git_run(git_repo, "commit", "--allow-empty", "-m", "criterion outside date/path")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    instant = datetime.fromisoformat("2025-01-02T00:00:00+00:00")

    for path in (original, renamed):
        page = service.search(scope, SearchQuery("criterion", path, instant, instant))
        assert [hit.commit.oid for hit in page.hits] == [wanted]
        assert set(page.hits[0].matched_fields) >= {"message", "path", "author-date"}
        assert page.complete


@pytest.mark.parametrize("populated", [False, True])
def test_search_rejects_invalid_literal_path_on_every_scope(git_repo, git_run, populated):
    if populated:
        git_run(git_repo, "commit", "--allow-empty", "-m", "one")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(GitError, match="(?i)path|relative"):
        service.search(scope, SearchQuery(path="../outside.txt"))


def test_search_matches_author_email_sha_and_captured_ref(git_repo, git_run):
    git_run(
        git_repo,
        "commit",
        "--allow-empty",
        "--author=Alice Search <alice.search@example.invalid>",
        "-m",
        "identity commit",
    )
    oid = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    cases = (
        ("ALICE SEARCH", "author"),
        ("search@example.invalid", "email"),
        (oid[7:25].upper(), "sha"),
        ("main", "ref"),
        ("heads/main", "ref"),
    )
    for text, field in cases:
        page = service.search(scope, SearchQuery(text=text))
        assert [hit.commit.oid for hit in page.hits] == [oid]
        assert field in page.hits[0].matched_fields
    ref_hit = service.search(scope, SearchQuery(text="refs/heads/main")).hits[0]
    assert ref_hit.matching_refs == ("refs/heads/main",)


def test_search_supports_path_only_and_inclusive_date_only(git_repo, git_run, monkeypatch):
    path = "literal?[notes].txt"
    instant = "2025-02-03T04:05:06+00:00"
    monkeypatch.setenv("GIT_AUTHOR_DATE", instant)
    (git_repo / path).write_text("content\n")
    git_run(git_repo, "--literal-pathspecs", "add", "--", path)
    git_run(git_repo, "commit", "-m", "dated path")
    wanted = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2025-02-04T04:05:06+00:00")
    git_run(git_repo, "commit", "--allow-empty", "-m", "later")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    boundary = datetime.fromisoformat(instant)

    path_page = service.search(scope, SearchQuery(path=path))
    date_page = service.search(
        scope, SearchQuery(authored_from=boundary, authored_through=boundary)
    )

    assert [hit.commit.oid for hit in path_page.hits] == [wanted]
    assert path_page.hits[0].matched_fields == ("path",)
    assert [hit.commit.oid for hit in date_page.hits] == [wanted]
    assert date_page.hits[0].matched_fields == ("author-date",)


def test_search_reports_empty_batch_progress_until_complete(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "deep target")
    target = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    for index in range(4):
        git_run(git_repo, "commit", "--allow-empty", "-m", f"noise {index}")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    cursor = None
    pages = []
    while True:
        page = service.search(scope, SearchQuery(text="deep target"), cursor=cursor, scan_limit=1)
        pages.append(page)
        if page.complete:
            break
        cursor = page.next_cursor

    assert [page.examined_total for page in pages] == [1, 2, 3, 4, 5]
    assert all(page.hits == () for page in pages[:-1])
    assert all(page.next_cursor for page in pages[:-1])
    assert [hit.commit.oid for hit in pages[-1].hits] == [target]
    assert pages[-1].next_cursor is None


def test_author_time_search_hit_cursors_jump_from_first_and_later_rows(
    git_repo, git_run, monkeypatch
):
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2025-01-01T00:00:00+00:00")
    git_run(git_repo, "commit", "--allow-empty", "-m", "jump older")
    older = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00+00:00")
    git_run(git_repo, "commit", "--allow-empty", "-m", "jump first")
    first = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    result = service.search(scope, SearchQuery(text="jump"), order="author-time")

    assert [hit.commit.oid for hit in result.hits] == [first, older]
    for offset, hit in enumerate(result.hits):
        page = service.history(scope, order="author-time", cursor=hit.history_cursor, limit=1)
        assert page.omitted_before == offset
        assert page.commits[0].oid == hit.commit.oid


def test_search_cursor_rejects_changed_query_order_scope_and_mode(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "needle old")
    old = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    git_run(git_repo, "commit", "--allow-empty", "-m", "needle new")
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    cursor = service.search(scope, SearchQuery(text="needle"), scan_limit=1).next_cursor
    assert cursor

    with pytest.raises(ValueError, match="cursor"):
        service.search(scope, SearchQuery(text="changed"), cursor=cursor)
    with pytest.raises(ValueError, match="cursor"):
        service.search(scope, SearchQuery(text="needle"), order="author-time", cursor=cursor)
    other_scope = service.capture((old,))
    with pytest.raises(ValueError, match="cursor"):
        service.search(other_scope, SearchQuery(text="needle"), cursor=cursor)
    history_cursor = service.history(scope, limit=1).next_cursor
    assert history_cursor
    with pytest.raises(ValueError, match="cursor"):
        service.search(scope, SearchQuery(text="needle"), cursor=history_cursor)


def test_search_uses_frozen_refs_and_reports_changed_state(git_repo, git_run):
    git_run(git_repo, "commit", "--allow-empty", "-m", "captured")
    captured = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()
    git_run(git_repo, "commit", "--allow-empty", "-m", "new captured")
    moved = git_run(git_repo, "rev-parse", "HEAD").stdout.strip()

    page = service.search(scope, SearchQuery(text="refs/heads/main"))

    assert [hit.commit.oid for hit in page.hits] == [captured]
    assert moved not in {hit.commit.oid for hit in page.hits}
    assert page.hits[0].matching_refs == ("refs/heads/main",)
    assert not page.state.current
    assert page.state.available


@pytest.mark.parametrize("scan_limit", [True, False, 0, -1, 1001, 1.5])
def test_search_rejects_invalid_scan_limit(git_repo, scan_limit):
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(ValueError, match="limit"):
        service.search(scope, SearchQuery(text="x"), scan_limit=scan_limit)


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(),
        SearchQuery(authored_from=datetime(2025, 1, 1)),
        SearchQuery(
            authored_from=datetime(2025, 1, 2, tzinfo=UTC),
            authored_through=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        SearchQuery(path=7),  # type: ignore[arg-type]
        "not a query",
    ],
)
def test_search_rejects_invalid_queries(git_repo, query):
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(ValueError, match="Search|search"):
        service.search(scope, query)


def test_search_rejects_invalid_order(git_repo):
    service = InvestigationService(Repository(git_repo))
    scope = service.capture()

    with pytest.raises(ValueError, match="order"):
        service.search(scope, SearchQuery(text="x"), order="date")  # type: ignore[arg-type]
