"""Background ingestion: R2 original -> page-addressable evidence rows.

The original filing stays immutable in R2.  Everything replaced here is
derived processing output, so a failed or upgraded ingestion can be rerun
without duplicating pages, sections, tables, facts or chunks.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .answering import embed_texts
from .html_xbrl import FilingExtract, parse_html_xbrl
from .pdf_fallback import parse_pdf_fallback
from .r2 import download
from .supabase_repository import SupabaseRepository


def _stable_id(document_id: str, kind: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"analyst-copilot:{document_id}:{kind}:{key}"))


def _job_update(
    db: SupabaseRepository,
    document_id: str,
    *,
    stage: str,
    progress: int,
    status: str = "processing",
    error: str | None = None,
    job_id: str | None = None,
) -> None:
    payload = {
        "status": status,
        "stage": stage,
        "progress": progress,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["error"] = error[:1000] if error is not None else None
    where = {"id": f"eq.{job_id}"} if job_id else {"document_id": f"eq.{document_id}"}
    db.update("processing_jobs", where, payload)


def _parse(local_path: Path, media_type: str) -> FilingExtract:
    if media_type == "application/pdf":
        return parse_pdf_fallback(local_path)
    return parse_html_xbrl(local_path)


def _persist_extract(db: SupabaseRepository, document_id: str, extract: FilingExtract) -> list[dict]:
    """Replace derived content with stable ids so retry/reprocess is safe."""
    if not extract.pages or not extract.sections:
        raise RuntimeError("No page-addressable filing content was extracted")
    db.clear_processed_content(document_id)
    page_ids = {page.number: _stable_id(document_id, "page", str(page.number)) for page in extract.pages}
    section_ids = {section.ordinal: _stable_id(document_id, "section", str(section.ordinal)) for section in extract.sections}
    section_for_page = {section.page_number: section.ordinal for section in extract.sections}

    db.insert_many(
        "document_pages",
        [
            {"id": page_ids[page.number], "document_id": document_id, "page_number": page.number, "source_anchor": page.anchor, "content": page.content}
            for page in extract.pages
        ],
    )
    db.insert_many(
        "document_sections",
        [
            {
                "id": section_ids[section.ordinal],
                "document_id": document_id,
                "page_id": page_ids[section.page_number],
                "page_number": section.page_number,
                "ordinal": section.ordinal,
                "heading": section.heading,
                "content": section.content,
                "source_anchor": section.source_anchor,
            }
            for section in extract.sections
        ],
    )
    db.insert_many(
        "document_tables",
        [
            {
                "id": _stable_id(document_id, "table", table.source_anchor),
                "document_id": document_id,
                "section_id": section_ids.get(section_for_page.get(table.page_number)),
                "page_number": table.page_number,
                "title": table.title,
                "content": {"rows": table.rows},
                "source_anchor": table.source_anchor,
            }
            for table in extract.tables
        ],
    )
    db.insert_many(
        "xbrl_facts",
        [
            {
                "id": _stable_id(document_id, "fact", f"{index}:{fact.source_anchor}:{fact.concept}:{fact.context_ref}"),
                "document_id": document_id,
                "section_id": section_ids.get(section_for_page.get(fact.page_number)),
                "concept": fact.concept,
                "context_ref": fact.context_ref,
                "value": fact.value,
                "normalized_value": fact.normalized_value,
                "scale": fact.scale,
                "sign": fact.sign,
                "unit": fact.unit,
                "decimals": fact.decimals,
                "period_start": fact.period_start,
                "period_end": fact.period_end,
                "instant_date": fact.instant_date,
                "page_number": fact.page_number,
                "source_anchor": fact.source_anchor,
            }
            for index, fact in enumerate(extract.facts, start=1)
        ],
    )
    chunks = [
        {
            # A filing can repeat an identical table/narrative fragment in
            # the same section. Include parse order as well as its content
            # hash so every persisted chunk has a unique stable primary key
            # across a retry/reprocess.
            "id": _stable_id(document_id, "chunk", f"{index}:{chunk.section_ordinal}:{chunk.content_type}:{hashlib.sha256(chunk.content.encode()).hexdigest()}"),
            "document_id": document_id,
            "section_id": section_ids[chunk.section_ordinal],
            "page_number": chunk.page_number,
            "content": chunk.content,
            "content_type": chunk.content_type,
        }
        for index, chunk in enumerate(extract.chunks, start=1)
    ]
    db.insert_many("document_chunks", chunks)
    return chunks


def process_document(document_id: str, storage_key: str, media_type: str, job_id: str | None = None) -> None:
    """Run a resumable-friendly ingestion with honest observable stages."""
    db = SupabaseRepository()
    now = lambda: datetime.now(timezone.utc).isoformat()
    try:
        db.update("documents", {"id": f"eq.{document_id}"}, {"status": "processing", "processing_error": None})
        _job_update(db, document_id, stage="reading_filing", progress=15, job_id=job_id)
        with tempfile.TemporaryDirectory(prefix="analyst-copilot-") as directory:
            extension = ".pdf" if media_type == "application/pdf" else ".htm"
            local_path = Path(directory) / f"filing{extension}"
            download(storage_key, str(local_path))
            _job_update(db, document_id, stage="extracting_sections", progress=32, job_id=job_id)
            extract = _parse(local_path, media_type)

        _job_update(db, document_id, stage="extracting_tables", progress=48, job_id=job_id)
        # Parsing produces pages/sections/tables/facts together; the separate
        # stages are visible checkpoints for a user rather than fake progress.
        _job_update(db, document_id, stage="extracting_xbrl", progress=62, job_id=job_id)
        chunks = _persist_extract(db, document_id, extract)
        _job_update(db, document_id, stage="building_search_index", progress=74, job_id=job_id)

        embeddings_failed = False
        total = max(len(chunks), 1)
        for start in range(0, len(chunks), 10):
            batch = chunks[start : start + 10]
            try:
                vectors = embed_texts([row["content"] for row in batch])
                for row, vector in zip(batch, vectors):
                    row["embedding"] = vector
                db.upsert_many("document_chunks", batch)
            except Exception:
                # Lexical/table/XBRL retrieval remains useful when an optional
                # embedding call is unavailable.  Do not turn a usable filing
                # into a failed upload solely for that reason.
                embeddings_failed = True
                break
            progress = 74 + int(((start + len(batch)) / total) * 23)
            _job_update(db, document_id, stage="building_search_index", progress=min(progress, 97), job_id=job_id)

        note = "Embeddings were unavailable; using lexical, table, and Inline XBRL retrieval." if embeddings_failed else None
        db.update("documents", {"id": f"eq.{document_id}"}, {"status": "ready", "processed_at": now(), "processing_error": note})
        _job_update(db, document_id, stage="complete", progress=100, status="ready", job_id=job_id)
    except Exception as error:
        db.update("documents", {"id": f"eq.{document_id}"}, {"status": "failed", "processing_error": str(error)[:1000]})
        _job_update(db, document_id, stage="failed", progress=100, status="failed", error=str(error), job_id=job_id)
