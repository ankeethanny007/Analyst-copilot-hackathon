"""Evidence-bounded OpenAI answer generation."""

from __future__ import annotations

import os
import json
import re
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
    return embed_texts([question])[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    payload = _openai_post("/embeddings", {"model": model, "input": texts, "dimensions": 1536})
    return [item["embedding"] for item in payload["data"]]


def generate_answer(question: str, evidence: list[RetrievedEvidence]) -> tuple[str, str]:
    """Return answer/status; the model may only use labelled supplied excerpts."""
    if not evidence:
        return ABSTENTION, "not_found"
    direct = _direct_metric_answer(question, evidence)
    if direct:
        return direct, "supported"
    growth = _direct_growth_answer(question, evidence)
    if growth:
        return growth, "supported"
    sources = "\n\n".join(
        f"[S{i + 1}: Page {item.page_number} · {item.heading}]\n{item.excerpt}"
        for i, item in enumerate(evidence)
    )
    instructions = (
        "You are an evidence-first financial filing assistant. Answer only from the supplied source excerpts. "
        f"If they do not support a direct answer, reply exactly: {ABSTENTION} "
        "Do not use outside knowledge or invent numbers, dates, units, calculations, or citations. "
        "Match the FinanceBench-style answer format: for information extraction, give the exact requested value or fact in the first sentence and cite it as [S#]. "
        "For numerical or logical reasoning (including growth, margins, or changes), give a one-sentence conclusion followed by a `Calculation:` line that states only the inputs and arithmetic supported by sources. "
        "For drivers of a change, identify only the causes explicitly discussed in the supplied management discussion or notes, and cite each source. "
        "Do not restate the question, and never cite a source label that was not supplied."
    )
    response = _openai_post("/responses", {"model": os.getenv("OPENAI_MODEL", "gpt-5"), "instructions": instructions, "input": f"Question: {question}\n\nSources:\n{sources}"})
    text = response.get("output_text", "").strip()
    if not text or text == ABSTENTION:
        return ABSTENTION, "not_found"
    return text, "supported"


def _direct_metric_answer(question: str, evidence: list[RetrievedEvidence]) -> str | None:
    """Return an exact reported metric when the question and source align.

    This deterministic path prevents a generative abstention from hiding an
    unambiguous value already present in the filing evidence.
    """
    words = re.findall(r"[a-zA-Z]{3,}", question.lower())
    candidates = [" ".join(words[index:index + size]) for size in range(min(4, len(words)), 1, -1) for index in range(len(words) - size + 1)]
    for item_index, item in enumerate(evidence, start=1):
        text = " ".join(item.excerpt.split())
        for metric in candidates:
            match = re.search(rf"\b({re.escape(metric)})\b\s*\(?[a-z]?\)?\s*\$?\s*([\d][\d,]*(?:\.\d+)?)", text, re.IGNORECASE)
            if not match:
                continue
            label = match.group(1)
            value = match.group(2)
            unit = " million" if re.search(r"\bin millions\b", text, re.IGNORECASE) else ""
            return f"Answer: {label[0].upper() + label[1:]} was ${value}{unit}. [S{item_index}]"
    return None


def _direct_growth_answer(question: str, evidence: list[RetrievedEvidence]) -> str | None:
    """Calculate a year-over-year change only from a single cited table."""
    if not re.search(r"\b(growth|increase|decrease|decline|change)\b", question, re.I):
        return None
    years = sorted(re.findall(r"\b(20\d{2})\b", question), reverse=True)
    if len(years) < 2:
        return None
    for index, item in enumerate(evidence, start=1):
        text = " ".join(item.excerpt.split())
        if not all(year in text for year in years):
            continue
        match = re.search(r"\b(net sales|revenues?|gross profit)\b\s*(?:\([^)]*\))?(?:\s*\|\s*\$?)*\s*\(?([\d][\d,]*)\)?(?:\s*\|\s*\$?)*\s*\(?([\d][\d,]*)\)?", text, re.I)
        if not match:
            continue
        latest, prior = (int(value.replace(",", "")) for value in match.group(2, 3))
        if prior == 0:
            continue
        growth = (latest - prior) / prior * 100
        label = match.group(1).lower()
        display_label = "Net sales" if label in {"revenue", "revenues", "net sales"} else match.group(1).title()
        qualifier = " The filing reports net sales rather than gross revenue." if "gross revenue" in question.lower() and label == "net sales" else ""
        unit = " million" if re.search(r"\b(?:dollars?\s+)?(?:in\s+)?millions\b", text, re.I) else ""
        direction = "increased" if growth >= 0 else "decreased"
        return f"{display_label} {direction} {abs(growth):.1f}% from ${prior:,}{unit} in {years[1]} to ${latest:,}{unit} in {years[0]}.{qualifier} Calculation: (${latest:,} − ${prior:,}) ÷ ${prior:,} = {growth:.1f}%. [S{index}]"
    return None


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
