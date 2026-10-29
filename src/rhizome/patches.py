"""Canonical, byte-preserving line selection for Git staging.

The terminal UI may render :class:`PatchLine` objects however it likes.  Applying a
selection always uses the immutable canonical patch and repository identities captured
by :meth:`PatchService.open`; rendered or transformed text is never parsed back.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .git import GitError, Repository

_MAX_PATCH_BYTES = 2 * 1024 * 1024
_MAX_BLOB_BYTES = 16 * 1024 * 1024
_HUNK_RE = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class PatchLine:
    """One stable displayed line from a canonical unified patch."""

    id: int
    kind: Literal["context", "add", "delete", "hunk", "meta"]
    text: str
    hunk: int | None = None
    old_line: int | None = None
    new_line: int | None = None


@dataclass(frozen=True)
class _PatchItem:
    line: PatchLine
    content: bytes = b""


@dataclass(frozen=True)
class _Hunk:
    number: int
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    items: tuple[_PatchItem, ...]


@dataclass(frozen=True)
class _Identity:
    root: str
    head: str
    index: str
    worktree: str
    old_oid: str
    new_oid: str
    old_mode: str
    new_mode: str
    old_exists: bool
    new_exists: bool


@dataclass(frozen=True)
class PatchDocument:
    """An immutable canonical patch view and the state needed to apply it safely.

    ``lines`` is the display contract.  The private immutable fields retain exact
    patch bytes, parsed byte payloads, endpoint data, and repository fingerprints;
    callers must pass this same document back to :meth:`PatchService.apply`.
    """

    path: str
    staged: bool
    lines: tuple[PatchLine, ...]
    selectable: bool
    reason: str
    _context: int
    _raw_patch: bytes
    _items: tuple[_PatchItem, ...]
    _hunks: tuple[_Hunk, ...]
    _old_data: bytes
    _old_mode: str
    _new_mode: str
    _old_exists: bool
    _new_exists: bool
    _identity: _Identity


class PatchService:
    """Open and apply exact contiguous line selections against one repository."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def open(self, path: str, *, staged: bool = False, context: int = 3) -> PatchDocument:
        """Capture a canonical patch for *path* and return an immutable display model."""
        selected_path = self.repo._path(path)
        if not isinstance(staged, bool):
            raise GitError("Staged must be true or false.")
        if not isinstance(context, int) or isinstance(context, bool) or not 0 <= context <= 10_000:
            raise GitError("Diff context must be an integer between 0 and 10000.")
        return self._open(selected_path, staged=staged, context=context)

    def apply(self, document: PatchDocument, start: int, end: int) -> str:
        """Apply selected inclusive, contiguous displayed line IDs to the index only."""
        if not isinstance(document, PatchDocument):
            raise GitError("A PatchDocument from PatchService.open is required.")
        if not document.selectable:
            raise GitError(document.reason or "This patch does not support line selection.")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start > end
        ):
            raise GitError("Select an inclusive contiguous line range.")

        ids = {item.line.id for item in document._items}
        requested = set(range(start, end + 1))
        if not requested or not requested.issubset(ids):
            raise GitError("The selected line range is outside this patch.")
        selected_changes = {
            item.line.id
            for item in document._items
            if item.line.id in requested and item.line.kind in {"add", "delete"}
        }
        if not selected_changes:
            raise GitError("The selected range must contain at least one changed line.")

        current = self._open(document.path, staged=document.staged, context=document._context)
        self._require_current(document, current)

        all_changes = {
            item.line.id for item in document._items if item.line.kind in {"add", "delete"}
        }
        # A staged patch is HEAD -> index.  Unstaging selected lines means retaining
        # every *unselected* change from that patch, rather than applying a naïvely
        # reversed fragment to an index which may contain adjacent unrelated changes.
        included = all_changes - selected_changes if document.staged else selected_changes
        data = self._apply_changes(document, included)

        if not included:
            exists = document._old_exists
            mode = document._old_mode
        elif included == all_changes:
            exists = document._new_exists
            mode = document._new_mode
        else:
            exists = True
            mode = document._old_mode or document._new_mode
        if exists and mode not in {"100644", "100755"}:
            raise GitError("The selected result does not have a supported regular-file mode.")

        if exists:
            blob = (
                self.repo._git(
                    ["hash-object", "-w", "--stdin"],
                    input_bytes=data,
                    operation="store selected patch result",
                )
                .stdout.decode("ascii")
                .strip()
            )
            index_record = f"{mode} {blob}\t".encode("ascii") + os.fsencode(document.path) + b"\x00"
        else:
            oid_length = max(
                40,
                len(document._identity.head),
                len(document._identity.old_oid),
                len(document._identity.new_oid),
            )
            index_record = (
                b"0 " + (b"0" * oid_length) + b"\t" + os.fsencode(document.path) + b"\x00"
            )

        def validate() -> None:
            current = self._open(document.path, staged=document.staged, context=document._context)
            self._require_current(document, current)

        # Hold the actual worktree index lock across final revalidation and atomic
        # publication; another Git writer cannot slip between those two steps.
        self.repo._replace_index_entries(index_record, validate)
        verb = "Unstaged" if document.staged else "Staged"
        return f"{verb} selected lines in {document.path}."

    def _open(self, path: str, *, staged: bool, context: int) -> PatchDocument:
        head = self.repo._head()
        index_digest = self._index_fingerprint()
        worktree_digest, work_kind, work_mode = self._worktree_fingerprint(path)
        old_mode, old_oid = self._head_entry(path, head) if staged else self._index_entry(path)
        new_mode, new_oid = self._index_entry(path) if staged else ("", "")

        statuses = self.repo._status()
        related = [change for change in statuses if path in {change.path, change.original_path}]
        reason = ""
        if any(change.conflicted for change in related):
            reason = "Files with unresolved conflicts require a whole-file conflict workflow."
        elif any(change.original_path for change in related):
            reason = "Renames and copies do not support line selection; use a whole-file action."
        elif work_kind in {"symlink", "other", "parent-symlink"}:
            reason = "Symlinks and non-regular worktree paths require a whole-file action."
        elif old_mode and old_mode not in {"100644", "100755"}:
            reason = "Non-regular index entries do not support textual line selection."
        elif new_mode and new_mode not in {"100644", "100755"}:
            reason = "Non-regular index entries do not support textual line selection."
        elif self._has_unsupported_attributes(path):
            reason = (
                "Custom filters, encodings, or binary diff attributes require a whole-file action."
            )

        if not staged:
            # This is only a content fingerprint. Canonical target bytes still come
            # from Git's patch, so built-in text/EOL conversion is honored.
            new_oid = self._worktree_index_target_oid(path, work_kind)
            if work_kind == "regular":
                new_mode = work_mode
            elif work_kind == "missing":
                new_mode = ""
        old_exists = bool(old_oid)
        new_exists = bool(new_oid) if staged else work_kind == "regular"
        eligible_untracked = any(change.path == path and change.index == "?" for change in related)
        if (
            not reason
            and not staged
            and not old_exists
            and work_kind == "regular"
            and not eligible_untracked
        ):
            reason = (
                "Ignored or repository-internal files do not support line selection; "
                "use an explicit whole-file workflow."
            )

        raw = b""
        if not reason:
            raw = self._diff(path, staged=staged, context=context, head=head, tracked=old_exists)
            if len(raw) > _MAX_PATCH_BYTES:
                reason = (
                    "This patch exceeds the 2 MiB line-selection limit; use a whole-file action."
                )
                raw = raw[: _MAX_PATCH_BYTES + 1]

        items, hunks = self._parse(raw)
        if not reason and self._mode_change(raw, old_mode, new_mode, old_exists, new_exists):
            reason = "File mode changes do not support line selection; use a whole-file action."
        if not reason and (b"GIT binary patch" in raw or b"Binary files " in raw):
            reason = "Binary files do not support line selection; use a whole-file action."
        if not reason and not raw:
            reason = "There are no staged changes." if staged else "There are no unstaged changes."
        if not reason and not any(item.line.kind in {"add", "delete"} for item in items):
            reason = "This change has no selectable textual lines; use a whole-file action."

        old_data = b""
        if old_exists and not reason:
            try:
                old_data = self._read_blob(old_oid)
            except GitError as error:
                reason = f"Cannot select lines from this oversized endpoint: {error}"
        if not reason:
            try:
                rebuilt = self._build_data(
                    old_data,
                    hunks,
                    {item.line.id for item in items if item.line.kind in {"add", "delete"}},
                )
                if staged and new_exists and rebuilt != self._read_blob(new_oid):
                    reason = (
                        "The staged patch did not reproduce the exact index blob; "
                        "use a whole-file action."
                    )
                elif staged and not new_exists and rebuilt:
                    reason = "The staged deletion was not exact; use a whole-file action."
            except GitError as error:
                reason = f"Canonical patch validation failed: {error} Use a whole-file action."

        identity = _Identity(
            root=os.fspath(self.repo.root),
            head=head,
            index=index_digest,
            worktree=worktree_digest,
            old_oid=old_oid,
            new_oid=new_oid,
            old_mode=old_mode,
            new_mode=new_mode,
            old_exists=old_exists,
            new_exists=new_exists,
        )
        return PatchDocument(
            path=path,
            staged=staged,
            lines=tuple(item.line for item in items),
            selectable=not reason,
            reason=reason,
            _context=context,
            _raw_patch=raw,
            _items=items,
            _hunks=hunks,
            _old_data=old_data,
            _old_mode=old_mode,
            _new_mode=new_mode,
            _old_exists=old_exists,
            _new_exists=new_exists,
            _identity=identity,
        )

    def _require_current(self, expected: PatchDocument, current: PatchDocument) -> None:
        # Validate the complete immutable document, including base bytes and hunk
        # coordinates, not only fingerprints that a reconstructed value can retain.
        if current != expected or not current.selectable:
            raise GitError("Repository content changed. Refresh the patch and select again.")

    def _index_fingerprint(self) -> str:
        data = self.repo._git(["ls-files", "--stage", "-z"], operation="fingerprint index").stdout
        return hashlib.sha256(data).hexdigest()

    def _worktree_fingerprint(self, path: str) -> tuple[str, str, str]:
        candidate = self.repo.root.joinpath(*Path(path).parts)
        current = self.repo.root
        for part in Path(path).parts[:-1]:
            current /= part
            try:
                if stat.S_ISLNK(os.lstat(current).st_mode):
                    return "parent-symlink", "parent-symlink", ""
            except FileNotFoundError:
                break
            except OSError as error:
                raise GitError(f"Could not inspect worktree path: {error}") from error
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            return "missing", "missing", ""
        except OSError as error:
            raise GitError(f"Could not inspect worktree path: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            return "symlink", "symlink", ""
        if not stat.S_ISREG(info.st_mode):
            return f"other:{info.st_mode:o}", "other", ""
        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as error:
            raise GitError(f"Could not read worktree path: {error}") from error
        mode = "100755" if info.st_mode & 0o111 else "100644"
        value = f"regular:{mode}:{info.st_size}:{digest.hexdigest()}"
        return value, "regular", mode

    def _index_entry(self, path: str) -> tuple[str, str]:
        result = self.repo._git(
            ["ls-files", "--stage", "-z", "--", path],
            literal_paths=True,
            operation="inspect index path",
        )
        matches: list[tuple[str, str, str]] = []
        encoded = os.fsencode(path)
        for record in result.stdout.split(b"\x00"):
            metadata, separator, name = record.partition(b"\t")
            if not separator or name != encoded:
                continue
            fields = metadata.decode("ascii").split()
            if len(fields) == 3:
                matches.append((fields[0], fields[1], fields[2]))
        if not matches:
            return "", ""
        if len(matches) != 1 or matches[0][2] != "0":
            return "", ""
        return matches[0][0], matches[0][1]

    def _head_entry(self, path: str, head: str) -> tuple[str, str]:
        if not head:
            return "", ""
        result = self.repo._git(
            ["ls-tree", "-z", head, "--", path],
            literal_paths=True,
            operation="inspect HEAD path",
        )
        encoded = os.fsencode(path)
        for record in result.stdout.split(b"\x00"):
            metadata, separator, name = record.partition(b"\t")
            if not separator or name != encoded:
                continue
            fields = metadata.decode("ascii").split()
            if len(fields) == 3 and fields[1] == "blob":
                return fields[0], fields[2]
        return "", ""

    def _worktree_index_target_oid(self, path: str, kind: str) -> str:
        if kind != "regular":
            return ""
        # Compute without -w: opening a patch must not mutate the object database.
        result = self.repo._git(
            ["hash-object", "--no-filters", "--", os.fspath(self.repo.root / path)],
            operation="fingerprint worktree path",
        )
        return result.stdout.decode("ascii").strip()

    def _has_unsupported_attributes(self, path: str) -> bool:
        result = self.repo._git(
            ["check-attr", "-z", "diff", "filter", "working-tree-encoding", "--", path],
            literal_paths=True,
            operation="inspect patch attributes",
        )
        fields = result.stdout.split(b"\x00")
        values: dict[bytes, bytes] = {}
        for offset in range(0, len(fields) - 2, 3):
            if fields[offset] == os.fsencode(path):
                values[fields[offset + 1]] = fields[offset + 2]
        diff = values.get(b"diff", b"unspecified")
        filter_value = values.get(b"filter", b"unspecified")
        encoding = values.get(b"working-tree-encoding", b"unspecified")
        return (
            diff not in {b"unspecified", b"set"}
            or filter_value not in {b"unspecified", b"unset"}
            or encoding not in {b"unspecified", b"unset"}
        )

    def _diff(self, path: str, *, staged: bool, context: int, head: str, tracked: bool) -> bytes:
        common = [
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--full-index",
            f"--unified={context}",
        ]
        if staged:
            old = head or self.repo._empty_tree()
            args = [*common, "--cached", old, "--", path]
            return self.repo._git_diff(
                args, literal_paths=True, operation="open staged patch"
            ).stdout
        if tracked:
            return self.repo._git_diff(
                [*common, "--", path], literal_paths=True, operation="open unstaged patch"
            ).stdout
        return self.repo._git_diff(
            [*common, "--no-index", "--", os.devnull, path],
            literal_paths=True,
            ok_codes=(0, 1),
            operation="open untracked patch",
        ).stdout

    def _read_blob(self, oid: str) -> bytes:
        size = self.repo._git(["cat-file", "-s", oid], operation="size patch blob").stdout
        try:
            length = int(size.strip())
        except ValueError as error:
            raise GitError("Git returned an invalid blob size.") from error
        if length > _MAX_BLOB_BYTES:
            raise GitError("A patch endpoint exceeds the 16 MiB line-selection limit.")
        return self.repo._git(["cat-file", "blob", oid], operation="read patch blob").stdout

    @staticmethod
    def _mode_change(
        raw: bytes, old_mode: str, new_mode: str, old_exists: bool, new_exists: bool
    ) -> bool:
        return (
            old_exists
            and new_exists
            and old_mode != new_mode
            or b"old mode " in raw
            or b"new mode " in raw
        )

    @staticmethod
    def _records(raw: bytes) -> list[bytes]:
        if not raw:
            return []
        pieces = raw.split(b"\n")
        records = [piece + b"\n" for piece in pieces[:-1]]
        if pieces[-1]:
            records.append(pieces[-1])
        return records

    def _parse(self, raw: bytes) -> tuple[tuple[_PatchItem, ...], tuple[_Hunk, ...]]:
        items: list[_PatchItem] = []
        hunk_specs: list[tuple[int, int, int, int, int, int]] = []
        hunk_number: int | None = None
        old_line = 0
        new_line = 0
        for record in self._records(raw):
            match = _HUNK_RE.match(record)
            line_id = len(items) + 1
            display = record.removesuffix(b"\n").decode("utf-8", "surrogateescape")
            if match:
                hunk_number = len(hunk_specs) + 1
                old_start, old_count, new_start, new_count = (
                    int(match.group(1)),
                    int(match.group(2) or b"1"),
                    int(match.group(3)),
                    int(match.group(4) or b"1"),
                )
                old_line = old_start
                new_line = new_start
                hunk_specs.append(
                    (hunk_number, old_start, old_count, new_start, new_count, len(items))
                )
                items.append(_PatchItem(PatchLine(line_id, "hunk", display, hunk=hunk_number)))
                continue
            if hunk_number is not None and record.startswith(b"\\ No newline at end of file"):
                if items and items[-1].line.kind in {"context", "add", "delete"}:
                    items[-1] = replace(items[-1], content=items[-1].content.removesuffix(b"\n"))
                items.append(_PatchItem(PatchLine(line_id, "meta", display, hunk=hunk_number)))
                continue
            if hunk_number is not None and record[:1] in {b" ", b"+", b"-"}:
                prefix = record[:1]
                content = record[1:]
                if prefix == b" ":
                    line = PatchLine(
                        line_id,
                        "context",
                        display,
                        hunk=hunk_number,
                        old_line=old_line,
                        new_line=new_line,
                    )
                    old_line += 1
                    new_line += 1
                elif prefix == b"-":
                    line = PatchLine(
                        line_id,
                        "delete",
                        display,
                        hunk=hunk_number,
                        old_line=old_line,
                    )
                    old_line += 1
                else:
                    line = PatchLine(
                        line_id,
                        "add",
                        display,
                        hunk=hunk_number,
                        new_line=new_line,
                    )
                    new_line += 1
                items.append(_PatchItem(line, content))
                continue
            items.append(_PatchItem(PatchLine(line_id, "meta", display)))

        hunks: list[_Hunk] = []
        for index, spec in enumerate(hunk_specs):
            number, old_start, old_count, new_start, new_count, first = spec
            last = hunk_specs[index + 1][5] if index + 1 < len(hunk_specs) else len(items)
            hunks.append(
                _Hunk(
                    number,
                    old_start,
                    old_count,
                    new_start,
                    new_count,
                    tuple(
                        item
                        for item in items[first + 1 : last]
                        if item.line.kind in {"context", "add", "delete"}
                    ),
                )
            )
        return tuple(items), tuple(hunks)

    @staticmethod
    def _split_data(data: bytes) -> list[bytes]:
        if not data:
            return []
        pieces = data.split(b"\n")
        lines = [piece + b"\n" for piece in pieces[:-1]]
        if pieces[-1]:
            lines.append(pieces[-1])
        return lines

    def _apply_changes(self, document: PatchDocument, included: set[int]) -> bytes:
        return self._build_data(document._old_data, document._hunks, included)

    def _build_data(self, old_data: bytes, hunks: tuple[_Hunk, ...], included: set[int]) -> bytes:
        old_lines = self._split_data(old_data)
        output: list[bytes] = []
        cursor = 0
        for hunk in hunks:
            start = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
            if start < cursor or start > len(old_lines):
                raise GitError("Git returned inconsistent hunk coordinates.")
            output.extend(old_lines[cursor:start])
            cursor = start
            seen_old = 0
            seen_new = 0
            for item in hunk.items:
                kind = item.line.kind
                if kind == "context":
                    if cursor >= len(old_lines) or old_lines[cursor] != item.content:
                        raise GitError("Patch context does not match its captured base blob.")
                    output.append(old_lines[cursor])
                    cursor += 1
                    seen_old += 1
                    seen_new += 1
                elif kind == "delete":
                    if cursor >= len(old_lines) or old_lines[cursor] != item.content:
                        raise GitError("Deleted line does not match its captured base blob.")
                    if item.line.id not in included:
                        output.append(old_lines[cursor])
                        seen_new += 1
                    cursor += 1
                    seen_old += 1
                elif kind == "add":
                    if item.line.id in included:
                        output.append(item.content)
                        seen_new += 1
            if seen_old != hunk.old_count:
                raise GitError("Git returned an inconsistent old hunk length.")
            # new_count describes the full patch, not a partial selected result.
            full_new = sum(item.line.kind in {"context", "add"} for item in hunk.items)
            if full_new != hunk.new_count:
                raise GitError("Git returned an inconsistent new hunk length.")
        output.extend(old_lines[cursor:])
        return b"".join(output)
