"""Background ingestion: R2 original -> evidence rows -> pgvector embeddings."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .answering import embed_texts
from .html_xbrl import parse_html_xbrl
from .r2 import download
from .supabase_repository import SupabaseRepository


def process_document(document_id: str, storage_key: str, media_type: str) -> None:
    db = SupabaseRepository()
    now = lambda: datetime.now(timezone.utc).isoformat()
    try:
        if media_type == "application/pdf":
            raise RuntimeError("PDF fallback ingestion is not enabled in this MVP build; upload HTML or Inline XBRL.")
        db.update("documents", {"id": f"eq.{document_id}"}, {"status": "processing", "processing_error": None})
        db.update("processing_jobs", {"document_id": f"eq.{document_id}"}, {"status": "processing", "stage": "reading_filing", "progress": 20, "updated_at": now()})
        with tempfile.TemporaryDirectory(prefix="analyst-copilot-") as directory:
            local_path = str(Path(directory) / "filing.htm")
            download(storage_key, local_path)
            extract = parse_html_xbrl(Path(local_path))
        page_ids = {page.number: str(uuid4()) for page in extract.pages}
        section_ids = {section.ordinal: str(uuid4()) for section in extract.sections}
        section_for_page = {section.page_number: section.ordinal for section in extract.sections}
        db.insert_many("document_pages", [{"id": page_ids[p.number], "document_id": document_id, "page_number": p.number, "source_anchor": p.anchor, "content": p.content} for p in extract.pages])
        db.insert_many("document_sections", [{"id": section_ids[s.ordinal], "document_id": document_id, "page_id": page_ids[s.page_number], "page_number": s.page_number, "ordinal": s.ordinal, "heading": s.heading, "content": s.content, "source_anchor": s.source_anchor} for s in extract.sections])
        db.insert_many("document_tables", [{"id": str(uuid4()), "document_id": document_id, "section_id": section_ids.get(section_for_page.get(t.page_number)), "page_number": t.page_number, "title": t.title, "content": {"rows": t.rows}, "source_anchor": t.source_anchor} for t in extract.tables])
        db.insert_many("xbrl_facts", [{"id": str(uuid4()), "document_id": document_id, "section_id": section_ids.get(section_for_page.get(f.page_number)), "concept": f.concept, "context_ref": f.context_ref, "value": f.value, "unit": f.unit, "decimals": f.decimals, "period_start": f.period_start, "period_end": f.period_end, "instant_date": f.instant_date, "page_number": f.page_number, "source_anchor": f.source_anchor} for f in extract.facts])
        chunks = [{"id": str(uuid4()), "document_id": document_id, "section_id": section_ids[c.section_ordinal], "page_number": c.page_number, "content": c.content, "content_type": c.content_type} for c in extract.chunks]
        db.insert_many("document_chunks", chunks)
        db.update("processing_jobs", {"document_id": f"eq.{document_id}"}, {"stage": "building_search_index", "progress": 80, "updated_at": now()})
        for start in range(0, len(chunks), 10):
            batch = chunks[start : start + 10]
            for row, vector in zip(batch, embed_texts([row["content"] for row in batch])): row["embedding"] = vector
            db.upsert_many("document_chunks", batch)
        db.update("documents", {"id": f"eq.{document_id}"}, {"status": "ready", "processed_at": now()})
        db.update("processing_jobs", {"document_id": f"eq.{document_id}"}, {"status": "ready", "stage": "complete", "progress": 100, "updated_at": now()})
    except Exception as error:
        db.update("documents", {"id": f"eq.{document_id}"}, {"status": "failed", "processing_error": str(error)[:1000]})
        db.update("processing_jobs", {"document_id": f"eq.{document_id}"}, {"status": "failed", "stage": "failed", "error": str(error)[:1000], "updated_at": now()})
