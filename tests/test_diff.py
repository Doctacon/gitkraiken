"""File-preview formatting must not change the patch or hide changed content."""

import pytest

from gitkraiken.app import patch_text

HEADER = (
    'diff --git "a/src/long [name].py" "b/src/long [name].py"\n'
    "index 1234567..89abcde 100644\n"
    '--- "a/src/long [name].py"\n'
    '+++ "b/src/long [name].py"\n'
)


def test_file_preview_omits_transport_headers_but_keeps_hunks_and_literal_content():
    body = (
        "@@ -1,3 +1,3 @@ def example():\n"
        " context\n"
        "-[red]old[/red]\n"
        "+[green]new[/green]\n"
        " unchanged\n"
        "@@ -20 +20 @@\n"
        "-before\n"
        "+after\n"
        "\\ No newline at end of file\n"
        "\n[diff truncated by GitKraiken]\n"
    )
    rendered = patch_text(HEADER + body)
    assert rendered.plain == body
    assert rendered.spans  # Hunk/add/remove colors still exist.


def test_header_like_content_is_preserved_inside_hunks():
    body = (
        "@@ -1,4 +1,4 @@\n"
        "--- removed text starting with two dashes\n"
        "+++ added text starting with two pluses\n"
        " diff --git a/literal b/literal\n"
        " index literal content\n"
        "-diff --git a/old b/old\n"
        "+diff --git a/new b/new\n"
    )
    assert patch_text(HEADER + body).plain == body


def test_each_file_header_is_hidden_in_a_multi_file_patch():
    first = "@@ -1 +1 @@\n-old\n+new\n"
    second = "@@ -9 +9 @@\n-before\n+after\n"
    assert patch_text(HEADER + first + HEADER + second).plain == first + second


@pytest.mark.parametrize(
    "summary",
    [
        "Binary files a/image.png and b/image.png differ\n",
        "similarity index 100%\nrename from old.txt\nrename to new.txt\n",
        "old mode 100644\nnew mode 100755\n",
        "new file mode 100644\n",
        "deleted file mode 100644\n",
    ],
)
def test_nontextual_change_summaries_remain_visible(summary):
    assert patch_text(HEADER + summary).plain == summary


@pytest.mark.parametrize("patch", ["", HEADER])
def test_empty_or_header_only_diff_has_a_visible_explanation(patch):
    assert patch_text(patch).plain == "No textual changes (mode-only change or empty file)."


@pytest.mark.parametrize(
    "message",
    [
        "Git read commit diff failed: [path] does not exist.",
        "index file is corrupt\nPlease repair the repository.",
        "No textual changes.",
    ],
)
def test_non_patch_messages_are_not_filtered(message):
    assert patch_text(message).plain == message
