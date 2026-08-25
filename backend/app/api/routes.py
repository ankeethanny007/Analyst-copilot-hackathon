from datetime import datetime, timezone
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services.ingestion import sha256_upload
from app.services.html_xbrl import parse_html_xbrl
from app.services.qa import answer
from app.services.answering import RetrievedEvidence, embed_question, generate_answer
from app.services.supabase_repository import SupabaseRepository
from app.services.auth import current_owner_id
from app.services.r2 import storage_key, upload

router = APIRouter()


class CreateTopic(BaseModel):
    document_id: UUID
    title: str = Field(min_length=1, max_length=120)


class RenameTopic(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class AskQuestion(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class LocalQuestion(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    filing_path: str


STOP_WORDS = {"a", "an", "and", "for", "in", "is", "of", "the", "to", "was", "what", "with"}


def repository() -> SupabaseRepository:
    try:
        return SupabaseRepository()
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


@router.post("/documents", status_code=202)
async def upload_document(file: UploadFile = File(...), owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    """Persist the immutable original once, then enqueue document processing."""
    if file.content_type not in {"text/html", "application/pdf", "application/xhtml+xml"}:
        raise HTTPException(415, "Only HTML/Inline XBRL or PDF filings are supported")
    checksum, size_bytes = await sha256_upload(file)
    existing = db.select("documents", {"owner_id": f"eq.{owner_id}", "sha256": f"eq.{checksum}", "select": "id,original_filename,status"})
    if existing:
        topic_rows = db.select("chat_topics", {"owner_id": f"eq.{owner_id}", "document_id": f"eq.{existing[0]['id']}", "select": "id,document_id,title", "order": "created_at.asc", "limit": "1"})
        return {"document": existing[0], "chat_topic": topic_rows[0] if topic_rows else None, "deduplicated": True}
    document_id = str(uuid4())
    key = storage_key(owner_id, checksum, file.filename or "filing")
    try:
        upload(file.file, key, file.content_type or "application/octet-stream")
        document = db.insert("documents", {"id": document_id, "owner_id": owner_id, "original_filename": file.filename or "filing", "media_type": file.content_type, "sha256": checksum, "storage_key": key, "status": "queued"})
        topic = db.insert("chat_topics", {"id": str(uuid4()), "owner_id": owner_id, "document_id": document_id, "title": Path(file.filename or "Filing").stem[:120]})
        db.insert("processing_jobs", {"id": str(uuid4()), "document_id": document_id, "status": "queued", "stage": "queued", "progress": 10})
    except Exception as error:
        raise HTTPException(502, f"Upload storage unavailable: {error}") from error
    return {
        "document": {**document, "size_bytes": size_bytes},
        "chat_topic": topic,
        "deduplicated": False,
    }


@router.get("/documents/{document_id}/status")
def document_status(document_id: UUID, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    rows = db.select("documents", {"id": f"eq.{document_id}", "owner_id": f"eq.{owner_id}", "select": "id,status,processing_error,processed_at"})
    if not rows:
        raise HTTPException(404, "Document not found")
    jobs = db.select("processing_jobs", {"document_id": f"eq.{document_id}", "select": "stage,progress,status", "order": "created_at.desc", "limit": "1"})
    return {"document": rows[0], "job": jobs[0] if jobs else None}


@router.get("/chat-topics")
def list_topics(owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> list[dict]:
    return db.select("chat_topics", {"owner_id": f"eq.{owner_id}", "select": "id,document_id,title,created_at,updated_at,documents(original_filename,status)", "order": "updated_at.desc"})


@router.post("/chat-topics", status_code=201)
def create_topic(payload: CreateTopic, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    document = db.select("documents", {"id": f"eq.{payload.document_id}", "owner_id": f"eq.{owner_id}", "select": "id"})
    if not document:
        raise HTTPException(404, "Document not found")
    return db.insert("chat_topics", {"owner_id": owner_id, **payload.model_dump(mode="json")})


@router.patch("/chat-topics/{topic_id}")
def rename_topic(topic_id: UUID, payload: RenameTopic, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    updated = db.update("chat_topics", {"id": f"eq.{topic_id}", "owner_id": f"eq.{owner_id}"}, {**payload.model_dump(), "updated_at": datetime.now(timezone.utc).isoformat()})
    if not updated:
        raise HTTPException(404, "Chat topic not found")
    return updated


@router.get("/chat-topics/{topic_id}/messages")
def list_messages(topic_id: UUID, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> list[dict]:
    if not db.topic_for_owner(str(topic_id), owner_id):
        raise HTTPException(404, "Chat topic not found")
    return db.select("messages", {"chat_topic_id": f"eq.{topic_id}", "select": "id,role,content,answer_status,created_at,message_evidence(ordinal,excerpt,document_sections(page_number,heading,source_anchor))", "order": "created_at.asc"})


@router.post("/chat-topics/{topic_id}/messages", status_code=201)
def ask_question(topic_id: UUID, payload: AskQuestion, owner_id: str = Depends(current_owner_id), db: SupabaseRepository = Depends(repository)) -> dict:
    topic = db.topic_for_owner(str(topic_id), owner_id)
    if not topic:
        raise HTTPException(404, "Chat topic not found")
    user_message = db.insert("messages", {"chat_topic_id": str(topic_id), "role": "user", "content": payload.content})
    try:
        matches = db.match_chunks(topic["document_id"], embed_question(payload.content), int(os.getenv("RETRIEVAL_TOP_K", "12")))
        threshold = float(os.getenv("EVIDENCE_MIN_SCORE", "0.55"))
        supported: list[dict] = []
        seen_sections: set[str] = set()
        for row in matches:
            section_id = row.get("section_id")
            if row["similarity"] < threshold or not section_id or section_id in seen_sections:
                continue
            supported.append(row)
            seen_sections.add(section_id)
            if len(supported) == 2:
                break
        sections = db.sections(topic["document_id"], [row["section_id"] for row in supported])
        evidence = [RetrievedEvidence(row["id"], row["section_id"], row["page_number"], sections[row["section_id"]].get("heading") or "Filing section", sections[row["section_id"]]["content"][:1200], float(row["similarity"])) for row in supported if row["section_id"] in sections]
        terms = [term for term in re.findall(r"[a-zA-Z]{3,}", payload.content.lower()) if term not in STOP_WORDS]
        phrase = " ".join(terms[:3])
        if phrase:
            for section in db.keyword_sections(topic["document_id"], phrase):
                if section["id"] in {item.section_id for item in evidence}:
                    continue
                evidence.append(RetrievedEvidence(None, section["id"], section["page_number"], section.get("heading") or "Filing section", section["content"][:1200], 1.0))
                if len(evidence) == 3:
                    break
        evidence.sort(key=lambda item: item.score, reverse=True)
        content, status = generate_answer(payload.content, evidence)
    except Exception as error:
        raise HTTPException(502, f"Answer service unavailable: {error}") from error
    assistant_message = db.insert("messages", {"chat_topic_id": str(topic_id), "role": "assistant", "content": content, "answer_status": status})
    for ordinal, item in enumerate(evidence, start=1):
        db.insert("message_evidence", {"message_id": assistant_message["id"], "ordinal": ordinal, "section_id": item.section_id, "chunk_id": item.chunk_id, "excerpt": item.excerpt})
    db.update("chat_topics", {"id": f"eq.{topic_id}", "owner_id": f"eq.{owner_id}"}, {"updated_at": datetime.now(timezone.utc).isoformat()})
    return {"user_message": user_message, "assistant_message": assistant_message, "evidence": [item.__dict__ for item in evidence]}


@router.post("/local/answer")
def local_answer(payload: LocalQuestion) -> dict:
    """Development-only QA path; production resolves a topic and owner on the server."""
    result = answer(payload.content, parse_html_xbrl(Path(payload.filing_path)))
    return {"status": result.status, "content": result.content, "source_summary": result.source_summary, "evidence": [item.__dict__ for item in result.evidence]}
