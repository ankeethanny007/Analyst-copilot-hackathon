"""Evidence-bounded OpenAI answer generation."""

from __future__ import annotations

import os
import json
import ssl
from dataclasses import dataclass
from urllib.request import Request, urlopen

import certifi


ABSTENTION = "I don't have sufficient evidence in this filing to answer that."
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


@dataclass
class RetrievedEvidence:
    chunk_id: str | None
    section_id: str
    page_number: int
    heading: str
    excerpt: str
    score: float


def embed_question(question: str) -> list[float]:
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    payload = _openai_post("/embeddings", {"model": model, "input": question, "dimensions": 1536})
    return payload["data"][0]["embedding"]


def generate_answer(question: str, evidence: list[RetrievedEvidence]) -> tuple[str, str]:
    """Return answer/status; the model may only use labelled supplied excerpts."""
    if not evidence:
        return ABSTENTION, "not_found"
    sources = "\n\n".join(
        f"[S{i + 1}: Page {item.page_number} · {item.heading}]\n{item.excerpt}"
        for i, item in enumerate(evidence)
    )
    instructions = (
        "You are an evidence-first financial filing assistant. Answer only from the supplied source excerpts. "
        f"If they do not support a direct answer, reply exactly: {ABSTENTION} "
        "Do not use outside knowledge or invent numbers, dates, or citations. Be concise and cite source labels like [S1]."
    )
    response = _openai_post("/responses", {"model": os.getenv("OPENAI_MODEL", "gpt-5"), "instructions": instructions, "input": f"Question: {question}\n\nSources:\n{sources}"})
    text = response.get("output_text", "").strip()
    if not text or text == ABSTENTION:
        return ABSTENTION, "not_found"
    return text, "supported"


def _openai_post(path: str, body: dict) -> dict:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    request = Request(
        f"https://api.openai.com/v1{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90, context=SSL_CONTEXT) as response:
        return json.loads(response.read())
