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
from urllib.request import Request, urlopen

import certifi

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class SupabaseRepository:
    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured on the backend")
        self.base_url = url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

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
        with urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            payload = response.read()
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

    def topic_for_owner(self, topic_id: str, owner_id: str) -> dict[str, Any] | None:
        rows = self.select("chat_topics", {"id": f"eq.{topic_id}", "owner_id": f"eq.{owner_id}", "select": "id,document_id,title,created_at,updated_at"})
        return rows[0] if rows else None

    def match_chunks(self, document_id: str, embedding: list[float], limit: int) -> list[dict[str, Any]]:
        return self._request("/rpc/match_document_chunks", "POST", {"p_document_id": document_id, "p_embedding": embedding, "p_limit": limit})

    def sections(self, document_id: str, section_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not section_ids:
            return {}
        rows = self.select("document_sections", {"document_id": f"eq.{document_id}", "id": f"in.({','.join(section_ids)})", "select": "id,page_number,heading,content,source_anchor"})
        return {row["id"]: row for row in rows}

    def keyword_sections(self, document_id: str, phrase: str) -> list[dict[str, Any]]:
        """Small lexical complement to semantic retrieval for exact filing terms."""
        return self.select("document_sections", {"document_id": f"eq.{document_id}", "content": f"ilike.*{phrase}*", "select": "id,page_number,heading,content", "order": "ordinal.asc", "limit": "6"})

    def document_filename(self, document_id: str, owner_id: str) -> str | None:
        rows = self.select("documents", {"id": f"eq.{document_id}", "owner_id": f"eq.{owner_id}", "select": "original_filename", "limit": "1"})
        return rows[0]["original_filename"] if rows else None

    def tables(self, document_id: str) -> list[dict[str, Any]]:
        return self.select("document_tables", {"document_id": f"eq.{document_id}", "select": "section_id,page_number,title,content", "order": "page_number.asc", "limit": "500"})
