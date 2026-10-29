from __future__ import annotations

import os

import pytest

from rhizome.git import Repository


@pytest.mark.parametrize("read", ("snapshot", "diff"))
def test_local_inspection_does_not_rewrite_index_metadata(git_repo, git_run, read):
    path = git_repo / "example.txt"
    path.write_text("base\n")
    git_run(git_repo, "add", path.name)
    git_run(git_repo, "commit", "-m", "base")
    before_head = git_run(git_repo, "rev-parse", "HEAD").stdout
    index = git_repo / ".git" / "index"
    before = index.read_bytes()
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
    repository = Repository(git_repo)

    if read == "snapshot":
        assert not repository.snapshot().files
    else:
        repository.diff(path.name)

    assert index.read_bytes() == before
    assert git_run(git_repo, "rev-parse", "HEAD").stdout == before_head
    assert path.read_text() == "base\n"
