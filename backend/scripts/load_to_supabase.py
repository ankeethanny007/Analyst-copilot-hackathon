"""Load an HTML/Inline XBRL filing into Supabase for development.

Requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and DEMO_OWNER_ID. It does
not upload the original filing to R2 and deliberately leaves the document in
`processing` until embeddings are generated.
"""

import hashlib
import json
import os
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import certifi

from app.services.html_xbrl import parse_html_xbrl

BASE_URL = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
OWNER_ID = os.environ["DEMO_OWNER_ID"]
HEADERS = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def request(method: str, table: str, payload=None, query=None):
    url = f"{BASE_URL}/{table}" + (f"?{urlencode(query)}" if query else "")
    body = json.dumps(payload).encode() if payload is not None else None
    try:
        with urlopen(Request(url, data=body, headers=HEADERS, method=method), timeout=45, context=SSL_CONTEXT) as response:
            return response.read()
    except HTTPError as error:
        raise RuntimeError(f"{method} {table}: {error.code} {error.read().decode()}") from error


def insert_batch(table: str, rows: list[dict], size: int = 250) -> None:
    for start in range(0, len(rows), size):
        request("POST", table, rows[start : start + size])


def main(filing: Path) -> None:
    checksum = hashlib.sha256(filing.read_bytes()).hexdigest()
    existing = json.loads(request("GET", "documents", query={"owner_id": f"eq.{OWNER_ID}", "sha256": f"eq.{checksum}", "select": "id"}))
    if existing:
        print(f"Already loaded: {existing[0]['id']}")
        return
    extract = parse_html_xbrl(filing)
    document_id = str(uuid4())
    request("POST", "documents", {"id": document_id, "owner_id": OWNER_ID, "original_filename": filing.name, "media_type": "text/html", "sha256": checksum, "storage_key": f"seed/{document_id}/{filing.name}", "company_name": "JPMorgan Chase & Co.", "filing_type": "10-Q", "status": "processing"})
    page_ids = {page.number: str(uuid4()) for page in extract.pages}
    section_ids = {section.ordinal: str(uuid4()) for section in extract.sections}
    insert_batch("document_pages", [{"id": page_ids[p.number], "document_id": document_id, "page_number": p.number, "source_anchor": p.anchor, "content": p.content} for p in extract.pages])
    insert_batch("document_sections", [{"id": section_ids[s.ordinal], "document_id": document_id, "page_id": page_ids[s.page_number], "page_number": s.page_number, "ordinal": s.ordinal, "heading": s.heading, "content": s.content, "source_anchor": s.source_anchor} for s in extract.sections])
    insert_batch("document_tables", [{"id": str(uuid4()), "document_id": document_id, "section_id": section_ids[next(s.ordinal for s in extract.sections if s.page_number == table.page_number)], "page_number": table.page_number, "title": table.title, "content": {"rows": table.rows}, "source_anchor": table.source_anchor} for table in extract.tables])
    insert_batch("xbrl_facts", [{"id": str(uuid4()), "document_id": document_id, "section_id": section_ids.get(next((s.ordinal for s in extract.sections if s.page_number == fact.page_number), 0)) or None, "concept": fact.concept, "context_ref": fact.context_ref, "value": fact.value, "unit": fact.unit, "decimals": fact.decimals, "period_start": fact.period_start, "period_end": fact.period_end, "instant_date": fact.instant_date, "page_number": fact.page_number, "source_anchor": fact.source_anchor} for fact in extract.facts])
    insert_batch("document_chunks", [{"id": str(uuid4()), "document_id": document_id, "section_id": section_ids[chunk.section_ordinal], "page_number": chunk.page_number, "content": chunk.content, "content_type": chunk.content_type} for chunk in extract.chunks])
    request("POST", "processing_jobs", {"id": str(uuid4()), "document_id": document_id, "status": "processing", "stage": "embedding_pending", "progress": 80})
    print(json.dumps({"document_id": document_id, **extract.summary(), "status": "processing", "stage": "embedding_pending"}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
