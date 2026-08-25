"""Owner-scoped filing, chat and evidence API routes."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services.answering import embed_question, generate_answer_result
from app.services.auth import current_owner_id
from app.services.hybrid_retrieval import rank_evidence
from app.services.ingestion import sha256_upload
from app.services.processing import process_document
from app.services.question_planning import plan_question
from app.services.r2 import storage_key, upload
from app.services.supabase_repository import SupabaseRepository

router = APIRouter()


class CreateTopic(BaseModel):
    document_id: UUID
    title: str = Field(min_length=1, max_length=120)


class RenameTopic(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class AskQuestion(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


def repository() -> SupabaseRepository:
    try:
        return SupabaseRepository()
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


def _filing_media_type(file: UploadFile) -> str:
    """Use a safe suffix fallback because browsers vary on `.htm` MIME types."""
    suffix = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if suffix in {".htm", ".html", ".xhtml"} and content_type in {"", "application/octet-stream", "text/html", "application/xhtml+xml"}:
        return "application/xhtml+xml" if suffix == ".xhtml" else "text/html"
    if suffix == ".pdf" and content_type in {"", "application/octet-stream", "application/pdf"}:
        return "application/pdf"
    raise HTTPException(415, "Upload an HTML/Inline XBRL or PDF filing")


def _topic_document(topic: dict, owner_id: str, db: SupabaseRepository) -> dict:
    document = db.document_for_owner(topic["document_id"], owner_id)
    if not document:
        raise HTTPException(404, "Filing not found")
    return document


def _cited_sources_for_persistence(result_content: str, evidence: list, citation_indices: tuple[int, ...]) -> tuple[str, list]:
    """Keep message citation labels aligned with the persisted source popup.

    Retrieval sources are intentionally ranked for the model but displayed in
    stable page order.  If the answer cites (for example) its fifth retrieved
    source, persisting only that source as popup item one must also rewrite
    ``[S5]`` to ``[S1]``.  Otherwise the answer and the compact source link
    would disagree after a chat is reloaded.
    """
    selected_indices = sorted({index for index in citation_indices if 1 <= index <= len(evidence)})
    cited = [evidence[index - 1] for index in selected_indices]
    remap = {original: ordinal for ordinal, original in enumerate(selected_indices, start=1)}

    def replace(match: re.Match[str]) -> str:
        source_index = int(match.group(1))
        return f"[S{remap[source_index]}]" if source_index in remap else match.group(0)

    return re.sub(r"\[S(\d+)\]", replace, result_content), cited


@router.post("/documents", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    owner_id: str = Depends(current_owner_id),
    db: SupabaseRepository = Depends(repository),
) -> dict:
    """Persist the immutable original once, then start document processing."""
    media_type = _filing_media_type(file)
    checksum, size_bytes = await sha256_upload(file)
    existing = db.select("documents", {"owner_id": f"eq.{owner_id}", "sha256": f"eq.{checksum}", "select": "id,original_filename,status"})
    if existing:
        topic_rows = db.select(
            "chat_topics",
            {
                "owner_id": f"eq.{owner_id}",
                "document_id": f"eq.{existing[0]['id']}",
                "select": "id,document_id,title",
                "order": "created_at.asc",
                "limit": "1",
            },
        )
        return {"document": existing[0], "chat_topic": topic_rows[0] if topic_rows else None, "deduplicated": True}

    document_id = str(uuid4())
    key = storage_key(owner_id, checksum, file.filename or "filing")
    try:
        upload(file.file, key, media_type)
        document = db.insert(
            "documents",
            {
                "id": document_id,
                "owner_id": owner_id,
                "original_filename": file.filename or "filing",
                "media_type": media_type,
                "sha256": checksum,
                "storage_key": key,
                "status": "queued",
            },
        )
        topic = db.insert(
            "chat_topics",
            {"id": str(uuid4()), "owner_id": owner_id, "document_id": document_id, "title": Path(file.filename or "Filing").stem[:120]},
        )
        job_id = str(uuid4())
        db.insert("processing_jobs", {"id": job_id, "document_id": document_id, "status": "queued", "stage": "queued", "progress": 5})
        background_tasks.add_task(process_document, document_id, key, media_type, job_id)
    except Exception as error:
        raise HTTPException(502, f"Upload storage unavailable: {error}") from error
    return {"document": {**document, "size_bytes": size_bytes}, "chat_topic": topic, "deduplicated": False}


@router.get("/documents")
def list_documents(owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> list[dict]:
    """List persistent owner filings separately from their lightweight chats."""
    return db.documents_for_owner(owner_id)


@router.get("/documents/{document_id}/status")
def document_status(document_id: UUID, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    document = db.document_for_owner(str(document_id), owner_id)
    if not document:
        raise HTTPException(404, "Document not found")
    jobs = db.select(
        "processing_jobs",
        {"document_id": f"eq.{document_id}", "select": "stage,progress,status,error,updated_at", "order": "created_at.desc", "limit": "1"},
    )
    return {"document": document, "job": jobs[0] if jobs else None}


@router.post("/documents/{document_id}/retry", status_code=202)
def retry_document_processing(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(current_owner_id),
    db: SupabaseRepository = Depends(repository),
) -> dict:
    """Retry a failed/ready filing without uploading another original copy."""
    rows = db.select(
        "documents",
        {
            "id": f"eq.{document_id}",
            "owner_id": f"eq.{owner_id}",
            "select": "id,status,storage_key,media_type,original_filename",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(404, "Document not found")
    document = rows[0]
    if document["status"] in {"queued", "processing"}:
        raise HTTPException(409, "This filing is already processing")
    job_id = str(uuid4())
    db.update("documents", {"id": f"eq.{document_id}", "owner_id": f"eq.{owner_id}"}, {"status": "queued", "processing_error": None})
    job = db.insert("processing_jobs", {"id": job_id, "document_id": str(document_id), "status": "queued", "stage": "queued", "progress": 5})
    background_tasks.add_task(process_document, str(document_id), document["storage_key"], document["media_type"], job_id)
    return {"document": {key: value for key, value in document.items() if key != "storage_key"}, "job": job}


@router.get("/documents/{document_id}/pages/{page_number}")
def document_page(document_id: UUID, page_number: int, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    """Serve one owner-authorized stored page for the source viewer."""
    if page_number < 1:
        raise HTTPException(404, "Page not found")
    page = db.document_page_for_owner(str(document_id), page_number, owner_id)
    if not page:
        raise HTTPException(404, "Page not found")
    return page


@router.get("/chat-topics")
def list_topics(owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> list[dict]:
    return db.select(
        "chat_topics",
        # `0003` deliberately adds a composite owner/document foreign key.
        # Avoid an embedded `documents(...)` relation here: PostgREST sees two
        # valid relationships and correctly treats the unhinted embed as
        # ambiguous. The filing library endpoint already supplies display
        # metadata, so a topic list only needs its own durable binding.
        {"owner_id": f"eq.{owner_id}", "select": "id,document_id,title,created_at,updated_at", "order": "updated_at.desc"},
    )


@router.post("/chat-topics", status_code=201)
def create_topic(payload: CreateTopic, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    document = db.document_for_owner(str(payload.document_id), owner_id)
    if not document:
        raise HTTPException(404, "Document not found")
    return db.insert("chat_topics", {"owner_id": owner_id, **payload.model_dump(mode="json")})


@router.patch("/chat-topics/{topic_id}")
def rename_topic(topic_id: UUID, payload: RenameTopic, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    updated = db.update(
        "chat_topics",
        {"id": f"eq.{topic_id}", "owner_id": f"eq.{owner_id}"},
        {**payload.model_dump(), "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if not updated:
        raise HTTPException(404, "Chat topic not found")
    return updated


@router.get("/chat-topics/{topic_id}/messages")
def list_messages(topic_id: UUID, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> list[dict]:
    if not db.topic_for_owner(str(topic_id), owner_id):
        raise HTTPException(404, "Chat topic not found")
    return db.select(
        "messages",
        {
            "chat_topic_id": f"eq.{topic_id}",
            "select": "id,role,content,answer_status,created_at,message_evidence(ordinal,excerpt,page_number,section_heading,source_anchor,source_type,table_id,table_title,document_sections(page_number,heading,source_anchor))",
            "order": "created_at.asc",
        },
    )


@router.get("/messages/{message_id}/evidence")
def message_evidence(message_id: UUID, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> list[dict]:
    evidence = db.message_evidence_for_owner(str(message_id), owner_id)
    if evidence is None:
        raise HTTPException(404, "Message not found")
    return evidence


@router.post("/chat-topics/{topic_id}/messages", status_code=201)
def ask_question(topic_id: UUID, payload: AskQuestion, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    """Retrieve and answer strictly within the document bound to this topic."""
    topic = db.topic_for_owner(str(topic_id), owner_id)
    if not topic:
        raise HTTPException(404, "Chat topic not found")
    document = _topic_document(topic, owner_id, db)
    if document.get("status") != "ready":
        stage = document.get("status", "processing")
        raise HTTPException(409, f"This filing is still {stage}. Wait for processing to complete before asking a question.")

    # Do not let an optional embedding/API failure turn a lexical/table/XBRL
    # answer into a false "not found" result.
    semantic_matches: list[dict] = []
    try:
        semantic_matches = db.match_chunks(
            topic["document_id"],
            embed_question(payload.content),
            int(os.getenv("RETRIEVAL_TOP_K", "20")),
        )
    except Exception:
        semantic_matches = []

    plan = plan_question(payload.content)
    sections = db.all_sections(topic["document_id"])
    evidence = rank_evidence(
        plan,
        sections=sections,
        semantic_matches=semantic_matches,
        tables=db.tables(topic["document_id"]),
        facts=db.relevant_xbrl_facts(topic["document_id"], plan.terms),
        limit=int(os.getenv("EVIDENCE_MAX_SOURCES", "7")),
    )
    result = generate_answer_result(payload.content, evidence)
    # Only persist source items actually cited by a supported answer. An
    # abstention has no link, while source metadata remains stable on reload.
    content, cited = (
        _cited_sources_for_persistence(result.content, evidence, result.citation_indices)
        if result.status == "supported"
        else (result.content, [])
    )
    user_message = db.insert("messages", {"chat_topic_id": str(topic_id), "role": "user", "content": payload.content})
    assistant_message = db.insert(
        "messages",
        {"chat_topic_id": str(topic_id), "role": "assistant", "content": content, "answer_status": result.status},
    )
    for ordinal, item in enumerate(cited, start=1):
        db.insert(
            "message_evidence",
            {
                "message_id": assistant_message["id"],
                "ordinal": ordinal,
                "section_id": item.section_id,
                "chunk_id": item.chunk_id,
                "excerpt": item.excerpt,
                "page_number": item.page_number,
                "section_heading": item.heading,
                "source_anchor": item.source_anchor,
                "source_type": item.source_type,
                "table_id": item.table_id,
                "table_title": item.table_title,
            },
        )
    db.update(
        "chat_topics",
        {"id": f"eq.{topic_id}", "owner_id": f"eq.{owner_id}"},
        {"updated_at": datetime.now(timezone.utc).isoformat()},
    )
    return {
        "user_message": user_message,
        "assistant_message": assistant_message,
        "evidence": [item.__dict__ for item in cited],
        "answer_status": result.status,
    }
