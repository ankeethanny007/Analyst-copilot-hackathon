"""Minimal server-only Supabase repository for the MVP API.

The browser never receives the service-role key.  Every read of a filing's
content begins by resolving the requested chat topic for the authenticated
owner, then uses that document ID for retrieval.
"""

from __future__ import annotations

import json
import os
import ssl
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import certifi

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _supabase_headers(key: str) -> dict[str, str]:
    """Build PostgREST headers for either legacy JWTs or modern secret keys.

    Supabase's ``sb_secret_*`` keys are API keys, not JWTs, so sending one as
    a Bearer token causes authentication to fail. Legacy service-role JWTs
    still require the Authorization header for the existing PostgREST flow.
    """
    headers = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _bounded_limit(value: int, *, default: int, maximum: int) -> int:
    """Keep broad filing reads predictably bounded before they reach PostgREST."""
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 1), maximum)


class SupabaseRepository:
    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured on the backend")
        self.base_url = url.rstrip("/") + "/rest/v1"
        self.headers = _supabase_headers(key)

    def _request(self, path: str, method: str = "GET", body: Any | None = None, prefer: str | None = None) -> Any:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        request = Request(
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                payload = response.read()
        except HTTPError as error:
            # PostgREST's error body identifies the constraint/table that
            # failed. Preserve that bounded diagnostic in a processing job
            # instead of reducing every database failure to "HTTP Error 409".
            detail = error.read().decode("utf-8", errors="replace")[:1500]
            raise RuntimeError(f"Supabase {method} {path} failed ({error.code}): {detail}") from error
        return json.loads(payload) if payload else None

    def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        return self._request(f"/{table}?{urlencode(params)}")

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        rows = self._request(f"/{table}", "POST", row, "return=representation")
        return rows[0]

    def insert_many(self, table: str, rows: list[dict[str, Any]]) -> None:
        for start in range(0, len(rows), 250):
            self._request(f"/{table}", "POST", rows[start : start + 250], "return=minimal")

    def upsert_many(self, table: str, rows: list[dict[str, Any]]) -> None:
        for start in range(0, len(rows), 25):
            self._request(f"/{table}?on_conflict=id", "POST", rows[start : start + 25], "resolution=merge-duplicates,return=minimal")

    def update(self, table: str, where: dict[str, str], row: dict[str, Any]) -> dict[str, Any] | None:
        rows = self._request(f"/{table}?{urlencode(where)}", "PATCH", row, "return=representation")
        return rows[0] if rows else None

    def delete(self, table: str, where: dict[str, str]) -> None:
        """Delete rows selected by explicit server-side filters.

        This is used only to replace derived, reproducible processing output;
        the immutable original filing and the user's messages remain intact.
        """
        self._request(f"/{table}?{urlencode(where)}", "DELETE", prefer="return=minimal")

    def clear_processed_content(self, document_id: str) -> None:
        """Remove replaceable derived rows before an idempotent reprocess.

        Migration 0002 snapshots evidence metadata and makes historical section
        links nullable, so answer history retains its exact displayed source.
        """
        where = {"document_id": f"eq.{document_id}"}
        for table in ("document_chunks", "xbrl_facts", "document_tables", "document_sections", "document_pages"):
            self.delete(table, where)

    def topic_for_owner(self, topic_id: str, owner_id: str) -> dict[str, Any] | None:
        rows = self.select("chat_topics", {"id": f"eq.{topic_id}", "owner_id": f"eq.{owner_id}", "select": "id,document_id,title,created_at,updated_at"})
        return rows[0] if rows else None

    def documents_for_owner(self, owner_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return an owner's filing library without exposing storage credentials or keys."""
        return self.select(
            "documents",
            {
                "owner_id": f"eq.{owner_id}",
                "select": "id,original_filename,media_type,company_name,filing_type,filing_period_end,status,processing_error,created_at,processed_at",
                "order": "created_at.desc",
                "limit": str(_bounded_limit(limit, default=100, maximum=200)),
            },
        )

    def document_for_owner(self, document_id: str, owner_id: str) -> dict[str, Any] | None:
        """Resolve one filing only when it belongs to the requested owner."""
        rows = self.select(
            "documents",
            {
                "id": f"eq.{document_id}",
                "owner_id": f"eq.{owner_id}",
                "select": "id,original_filename,media_type,company_name,filing_type,filing_period_end,status,processing_error,created_at,processed_at",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def document_page(self, document_id: str, page_number: int) -> dict[str, Any] | None:
        """Fetch a source page after the caller has already validated document ownership."""
        if isinstance(page_number, bool):
            return None
        try:
            page = int(page_number)
        except (TypeError, ValueError):
            return None
        if page < 1:
            return None
        rows = self.select(
            "document_pages",
            {
                "document_id": f"eq.{document_id}",
                "page_number": f"eq.{page}",
                "select": "id,document_id,page_number,source_anchor,content",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def document_page_for_owner(self, document_id: str, page_number: int, owner_id: str) -> dict[str, Any] | None:
        """Owner-scoped source-page lookup used by source/evidence endpoints."""
        if not self.document_for_owner(document_id, owner_id):
            return None
        return self.document_page(document_id, page_number)

    def match_chunks(self, document_id: str, embedding: list[float], limit: int) -> list[dict[str, Any]]:
        return self._request("/rpc/match_document_chunks", "POST", {"p_document_id": document_id, "p_embedding": embedding, "p_limit": limit})

    def sections(self, document_id: str, section_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not section_ids:
            return {}
        rows = self.select("document_sections", {"document_id": f"eq.{document_id}", "id": f"in.({','.join(section_ids)})", "select": "id,page_number,heading,content,source_anchor"})
        return {row["id"]: row for row in rows}

    def all_sections(self, document_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """Return ordered filing sections for hybrid retrieval, with a hard read cap."""
        return self.select(
            "document_sections",
            {
                "document_id": f"eq.{document_id}",
                "select": "id,page_number,ordinal,heading,content,source_anchor",
                "order": "ordinal.asc",
                "limit": str(_bounded_limit(limit, default=500, maximum=1000)),
            },
        )

    def keyword_sections(self, document_id: str, phrase: str) -> list[dict[str, Any]]:
        """Small lexical complement to semantic retrieval for exact filing terms."""
        return self.select("document_sections", {"document_id": f"eq.{document_id}", "content": f"ilike.*{phrase}*", "select": "id,page_number,heading,content", "order": "ordinal.asc", "limit": "6"})

    def document_filename(self, document_id: str, owner_id: str) -> str | None:
        rows = self.select("documents", {"id": f"eq.{document_id}", "owner_id": f"eq.{owner_id}", "select": "original_filename", "limit": "1"})
        return rows[0]["original_filename"] if rows else None

    def tables(self, document_id: str) -> list[dict[str, Any]]:
        return self.select("document_tables", {"document_id": f"eq.{document_id}", "select": "id,section_id,page_number,title,content,source_anchor", "order": "page_number.asc", "limit": "500"})

    def xbrl_facts(self, document_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """Return page-addressable XBRL facts for a validated document, with a hard cap."""
        return self.select(
            "xbrl_facts",
            {
                "document_id": f"eq.{document_id}",
                "select": "id,section_id,concept,context_ref,value,normalized_value,scale,sign,unit,decimals,period_start,period_end,instant_date,page_number,source_anchor",
                "order": "page_number.asc.nullslast,concept.asc",
                "limit": str(_bounded_limit(limit, default=500, maximum=1000)),
            },
        )

    def relevant_xbrl_facts(self, document_id: str, terms: tuple[str, ...] | list[str], limit: int = 300) -> list[dict[str, Any]]:
        """Retrieve question-relevant facts across a long Inline XBRL filing.

        A filing can contain thousands of facts.  Reading its first 500 pages
        of facts ordered by page silently misses a relevant fact later in the
        document.  Search the camel-case concept names with safe lexical terms
        first, then retain a compact early-fact fallback for unusual labels.
        """
        select = "id,section_id,concept,context_ref,value,normalized_value,scale,sign,unit,decimals,period_start,period_end,instant_date,page_number,source_anchor"
        safe_terms = list(
            dict.fromkeys(
                term.lower()
                for term in terms
                if len(term) >= 3 and term.replace("&", "").replace("-", "").isalnum()
            )
        )[:8]
        rows: list[dict[str, Any]] = []
        if safe_terms:
            conditions = ",".join(f"concept.ilike.*{term}*" for term in safe_terms)
            rows = self.select(
                "xbrl_facts",
                {
                    "document_id": f"eq.{document_id}",
                    "or": f"({conditions})",
                    "select": select,
                    "order": "page_number.asc.nullslast,concept.asc",
                    "limit": str(_bounded_limit(limit, default=300, maximum=600)),
                },
            )
        # A concise fallback preserves support for a question whose filing
        # concept uses a proprietary name unrelated to its user wording.
        fallback = self.xbrl_facts(document_id, limit=120)
        merged: dict[str, dict[str, Any]] = {}
        for row in [*rows, *fallback]:
            key = str(row.get("id") or f"{row.get('concept')}:{row.get('context_ref')}:{row.get('page_number')}")
            merged.setdefault(key, row)
        return list(merged.values())

    def message_evidence_for_owner(self, message_id: str, owner_id: str) -> list[dict[str, Any]] | None:
        """Return evidence only if the message belongs to an owner-owned topic.

        `None` means the message is not visible to the owner.  Snapshot fields
        are included first; embedded source records are retained as a fallback
        for answers written before migration 0002.  Evidence is intentionally
        unavailable for an abstention or failed answer: a source link must
        mean that the corresponding answer is supported by that source.
        """
        messages = self.select(
            "messages",
            {
                "id": f"eq.{message_id}",
                "select": "id,chat_topic_id,role,answer_status",
                "limit": "1",
            },
        )
        if not messages or not self.topic_for_owner(messages[0]["chat_topic_id"], owner_id):
            return None
        message = messages[0]
        if message.get("role") != "assistant" or message.get("answer_status") != "supported":
            return []
        return self.select(
            "message_evidence",
            {
                "message_id": f"eq.{message_id}",
                "select": "ordinal,excerpt,page_number,section_heading,source_anchor,source_type,table_id,table_title,document_sections(page_number,heading,source_anchor),document_tables(id,page_number,title,source_anchor)",
                "order": "ordinal.asc",
            },
        )
