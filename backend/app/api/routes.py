from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services.ingestion import sha256_upload
from app.services.html_xbrl import parse_html_xbrl
from app.services.qa import answer

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


@router.post("/documents", status_code=202)
async def upload_document(file: UploadFile = File(...)) -> dict:
    """Day 1 contract. Persist to R2, hash, then enqueue processing in implementation."""
    if file.content_type not in {"text/html", "application/pdf", "application/xhtml+xml"}:
        raise HTTPException(415, "Only HTML/Inline XBRL or PDF filings are supported")
    checksum, size_bytes = await sha256_upload(file)
    document_id = uuid4()
    topic_id = uuid4()
    return {
        "document": {"id": str(document_id), "filename": file.filename, "sha256": checksum, "size_bytes": size_bytes, "status": "queued"},
        "chat_topic": {"id": str(topic_id), "document_id": str(document_id), "title": file.filename},
        "deduplicated": False,
    }


@router.get("/documents/{document_id}/status")
def document_status(document_id: UUID) -> dict:
    return {"document_id": str(document_id), "status": "queued", "stage": "upload_complete", "progress": 10}


@router.post("/chat-topics", status_code=201)
def create_topic(payload: CreateTopic) -> dict:
    return {"id": str(uuid4()), **payload.model_dump(), "created_at": datetime.now(timezone.utc).isoformat()}


@router.patch("/chat-topics/{topic_id}")
def rename_topic(topic_id: UUID, payload: RenameTopic) -> dict:
    return {"id": str(topic_id), **payload.model_dump()}


@router.post("/chat-topics/{topic_id}/messages", status_code=202)
def ask_question(topic_id: UUID, payload: AskQuestion) -> dict:
    return {"message_id": str(uuid4()), "topic_id": str(topic_id), "content": payload.content, "status": "queued"}


@router.post("/local/answer")
def local_answer(payload: LocalQuestion) -> dict:
    """Development-only QA path; production resolves a topic and owner on the server."""
    result = answer(payload.content, parse_html_xbrl(Path(payload.filing_path)))
    return {"status": result.status, "content": result.content, "source_summary": result.source_summary, "evidence": [item.__dict__ for item in result.evidence]}
