"""Stable, service-owned entry points for captured Git investigations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import Any
from weakref import ReferenceType, ref

from rhizome.git import GitError, Repository
from rhizome.models import (
    BlamePage,
    ComparisonIntent,
    ComparisonResult,
    FileHistoryPage,
    HistoryOrder,
    HistoryPage,
    InvestigationScope,
    ScopeState,
    SearchHit,
    SearchPage,
    SearchQuery,
)


class InvestigationService:
    """Capture immutable repository scopes and validate their service ownership."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository
        self._scopes: dict[str, tuple[ReferenceType[InvestigationScope], InvestigationScope]] = {}
        self._cursor_secret = secrets.token_bytes(32)

    def capture(self, roots: Iterable[str] = ()) -> InvestigationScope:
        """Capture exact roots without allowing a string to masquerade as an iterable."""
        if isinstance(roots, (str, bytes)):
            raise ValueError("Investigation roots must be an iterable of root names.")
        try:
            normalized = tuple(roots)
        except TypeError as error:
            raise ValueError("Investigation roots must be an iterable of root names.") from error
        if any(not isinstance(root, str) or not root for root in normalized):
            raise ValueError("Each investigation root must be a non-empty string.")

        scope = self._repository.capture_investigation(normalized)
        scope_id = scope.scope_id

        def discard(scope_ref: ReferenceType[InvestigationScope]) -> None:
            owned = self._scopes.get(scope_id)
            if owned is not None and owned[0] is scope_ref:
                self._scopes.pop(scope_id, None)

        self._scopes[scope_id] = (ref(scope, discard), replace(scope))
        return scope

    def status(self, scope: InvestigationScope) -> ScopeState:
        """Return availability without consulting Git for foreign or altered scopes."""
        if not isinstance(scope, InvestigationScope):
            return self._unavailable_scope()
        owned = self._scopes.get(scope.scope_id)
        if owned is None or owned[0]() is not scope or owned[1] != scope:
            return self._unavailable_scope()
        return self._repository.investigation_status(scope)

    def history(
        self,
        scope: InvestigationScope,
        order: HistoryOrder = "topology",
        cursor: str | None = None,
        limit: int = 300,
    ) -> HistoryPage:
        """Read one bounded page from exact roots captured by this service."""
        scope = self._require_scope(scope)
        if order not in ("topology", "author-time"):
            raise ValueError("History order must be 'topology' or 'author-time'.")
        self._validate_limit(limit)

        offset = 0
        after = None
        if cursor is not None:
            offset, after = self._decode_cursor(
                cursor,
                scope_id=scope.scope_id,
                order=order,
                mode="history",
                context=None,
            )
        if order == "topology" and after is not None:
            raise ValueError("History cursor is invalid for topology order.")
        if order == "author-time" and offset > 0 and after is None:
            raise ValueError("History cursor is invalid for author-time order.")

        # The Repository API performs the pre-read availability/identity check.
        # Keep the post-read check below without duplicating the same preflight.
        commits, keys, has_more = self._repository.investigation_history(
            scope,
            order=order,
            offset=offset if order == "topology" else 0,
            after=after if order == "author-time" else None,
            limit=limit,
        )
        if len(keys) != len(commits) or (has_more and not commits):
            raise GitError("Git returned an invalid investigation history page.")

        state = self.status(scope)
        if not state.available:
            raise GitError(state.reason or "Investigation scope is unavailable.")
        next_cursor = None
        if has_more:
            next_after = keys[-1] if order == "author-time" else None
            next_cursor = self._encode_cursor(
                scope_id=scope.scope_id,
                order=order,
                mode="history",
                offset=offset + len(commits),
                after=next_after,
                context=None,
            )
        return HistoryPage(
            scope_id=scope.scope_id,
            order=order,
            commits=commits,
            next_cursor=next_cursor,
            has_more=has_more,
            omitted_before=offset,
            state=state,
        )

    def search(
        self,
        scope: InvestigationScope,
        query: SearchQuery,
        order: HistoryOrder = "topology",
        cursor: str | None = None,
        scan_limit: int = 1000,
    ) -> SearchPage:
        """Examine a bounded batch; an empty page is not necessarily a complete search."""
        scope = self._require_scope(scope)
        self._validate_limit(scan_limit)
        if order not in ("topology", "author-time"):
            raise ValueError("Search order must be 'topology' or 'author-time'.")
        if not isinstance(query, SearchQuery) or not isinstance(query.text, str):
            raise ValueError("Search requires a SearchQuery with text as a string.")
        if query.path is not None and not isinstance(query.path, str):
            raise ValueError("Search path must be a literal path string.")
        for value in (query.authored_from, query.authored_through):
            if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
                raise ValueError("Search author dates must be timezone-aware datetimes.")
        if (
            query.authored_from is not None
            and query.authored_through is not None
            and query.authored_from > query.authored_through
        ):
            raise ValueError("Search author-date range is reversed.")
        if not (
            query.text or query.path is not None or query.authored_from or query.authored_through
        ):
            raise ValueError("Search requires at least one criterion.")
        context = {
            "text": query.text,
            "path": query.path,
            "authored_from": query.authored_from.isoformat() if query.authored_from else None,
            "authored_through": query.authored_through.isoformat()
            if query.authored_through
            else None,
        }
        offset, after = (
            (0, None)
            if cursor is None
            else self._decode_cursor(
                cursor, scope_id=scope.scope_id, order=order, mode="search", context=context
            )
        )
        commits, keys, matches, has_more = self._repository.investigation_search(
            scope, query, order=order, offset=offset, after=after, scan_limit=scan_limit
        )
        state = self.status(scope)
        if not state.available:
            raise GitError(state.reason)
        hits = []
        for index, matched_fields, matching_refs in matches:
            previous_key = keys[index - 1] if index else after
            history_cursor = self._encode_cursor(
                scope_id=scope.scope_id,
                order=order,
                mode="history",
                offset=offset + index,
                after=previous_key if order == "author-time" else None,
                context=None,
            )
            hits.append(SearchHit(commits[index], matched_fields, matching_refs, history_cursor))
        examined_total = offset + len(commits)
        next_cursor = None
        if has_more:
            next_cursor = self._encode_cursor(
                scope_id=scope.scope_id,
                order=order,
                mode="search",
                offset=examined_total,
                after=keys[-1] if order == "author-time" else None,
                context=context,
            )
        return SearchPage(
            scope.scope_id,
            query,
            order,
            tuple(hits),
            len(commits),
            examined_total,
            next_cursor,
            not has_more,
            state,
        )

    def compare(
        self,
        scope: InvestigationScope,
        left_oid: str,
        right_oid: str,
        intent: ComparisonIntent,
    ) -> ComparisonResult:
        """Keep branch-introduced changes distinct from an explicit endpoint difference."""
        return self._repository.investigation_compare(
            self._require_scope(scope), left_oid, right_oid, intent
        )

    def file_history(
        self,
        scope: InvestigationScope,
        start_oid: str,
        path: str,
        cursor: str | None = None,
        limit: int = 300,
        follow_renames: bool = True,
    ) -> FileHistoryPage:
        scope = self._require_scope(scope)
        self._validate_limit(limit)
        if not isinstance(follow_renames, bool):
            raise ValueError("Follow renames must be a boolean.")
        context = {"revision": start_oid, "path": path, "follow": follow_renames}
        offset, _ = (
            (0, None)
            if cursor is None
            else self._decode_cursor(
                cursor,
                scope_id=scope.scope_id,
                order="topology",
                mode="file-history",
                context=context,
            )
        )
        entries, has_more, limitations, state = self._repository.investigation_file_history(
            scope, start_oid, path, offset=offset, limit=limit, follow_renames=follow_renames
        )
        next_cursor = (
            self._encode_cursor(
                scope_id=scope.scope_id,
                order="topology",
                mode="file-history",
                offset=offset + len(entries),
                after=None,
                context=context,
            )
            if has_more
            else None
        )
        return FileHistoryPage(
            scope.scope_id,
            start_oid,
            path,
            follow_renames,
            entries,
            next_cursor,
            not has_more,
            limitations,
            state,
        )

    def blame(
        self,
        scope: InvestigationScope,
        start_oid: str,
        path: str,
        cursor: str | None = None,
        limit: int = 300,
    ) -> BlamePage:
        scope = self._require_scope(scope)
        self._validate_limit(limit)
        context = {"revision": start_oid, "path": path}
        offset, _ = (
            (0, None)
            if cursor is None
            else self._decode_cursor(
                cursor, scope_id=scope.scope_id, order="topology", mode="blame", context=context
            )
        )
        lines, has_more, limitations, state = self._repository.investigation_blame(
            scope, start_oid, path, offset=offset, limit=limit
        )
        next_cursor = (
            self._encode_cursor(
                scope_id=scope.scope_id,
                order="topology",
                mode="blame",
                offset=offset + len(lines),
                after=None,
                context=context,
            )
            if has_more
            else None
        )
        return BlamePage(
            scope.scope_id, start_oid, path, lines, next_cursor, not has_more, limitations, state
        )

    def _require_scope(self, scope: InvestigationScope) -> InvestigationScope:
        """Validate scope ownership for later data-returning service queries."""
        if not isinstance(scope, InvestigationScope):
            raise GitError("Investigation scope is unavailable to this service.")
        owned = self._scopes.get(scope.scope_id)
        if owned is None or owned[0]() is not scope or owned[1] != scope:
            raise GitError("Investigation scope is unavailable to this service.")
        return scope

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("History limit must be an integer from 1 through 1000.")

    def _encode_cursor(
        self,
        *,
        scope_id: str,
        order: HistoryOrder,
        mode: str,
        offset: int,
        after: tuple[int, int] | None,
        context: Any,
    ) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "scope": scope_id,
                "order": order,
                "mode": mode,
                "offset": offset,
                "after": after,
                "context": context,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
        return f"{self._b64encode(payload)}.{self._b64encode(signature)}"

    def _decode_cursor(
        self,
        cursor: str,
        *,
        scope_id: str,
        order: HistoryOrder,
        mode: str,
        context: Any,
    ) -> tuple[int, tuple[int, int] | None]:
        try:
            if not isinstance(cursor, str):
                raise ValueError
            payload_token, signature_token = cursor.split(".")
            payload = self._b64decode(payload_token)
            signature = self._b64decode(signature_token)
            expected = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            value = json.loads(payload)
            if not isinstance(value, dict) or set(value) != {
                "v",
                "scope",
                "order",
                "mode",
                "offset",
                "after",
                "context",
            }:
                raise ValueError
            if (
                value["v"] != 1
                or value["scope"] != scope_id
                or value["order"] != order
                or value["mode"] != mode
                or value["context"] != context
                or isinstance(value["offset"], bool)
                or not isinstance(value["offset"], int)
                or value["offset"] < 0
            ):
                raise ValueError
            after_value = value["after"]
            if after_value is None:
                after = None
            elif (
                isinstance(after_value, list)
                and len(after_value) == 2
                and all(
                    isinstance(item, int) and not isinstance(item, bool) for item in after_value
                )
            ):
                after = (after_value[0], after_value[1])
            else:
                raise ValueError
            return value["offset"], after
        except (UnicodeError, ValueError, TypeError, KeyError) as error:
            raise ValueError("History cursor is invalid or does not match this query.") from error

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(value, canonical):
            raise ValueError("Cursor encoding is not canonical base64url.")
        return decoded

    @staticmethod
    def _unavailable_scope() -> ScopeState:
        return ScopeState(
            current=False,
            available=False,
            reason="Investigation scope is unavailable to this service.",
        )
