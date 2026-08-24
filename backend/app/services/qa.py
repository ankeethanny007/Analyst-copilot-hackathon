"""Evidence-first local retrieval used before vector search is configured."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .html_xbrl import Chunk, FilingExtract, Section

STOP_WORDS = {"a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is", "of", "on", "the", "to", "was", "what", "with"}


@dataclass
class Evidence:
    page_number: int
    heading: str
    excerpt: str
    score: float


@dataclass
class Answer:
    status: str
    content: str
    evidence: list[Evidence]

    @property
    def source_summary(self) -> str | None:
        if not self.evidence:
            return None
        first = self.evidence[0]
        suffix = f" +{len(self.evidence) - 1} more" if len(self.evidence) > 1 else ""
        return f"Page {first.page_number} · {first.heading}{suffix}"


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-zA-Z0-9]+", text.lower()) if len(term) > 1 and term not in STOP_WORDS}


def retrieve(question: str, extract: FilingExtract, limit: int = 3) -> list[Evidence]:
    terms = _terms(question)
    sections = {section.ordinal: section for section in extract.sections}
    scored: list[tuple[float, Chunk]] = []
    for chunk in extract.chunks:
        overlap = len(terms & _terms(chunk.content))
        if overlap:
            scored.append((overlap / max(len(terms), 1), chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    evidence: list[Evidence] = []
    seen_pages: set[int] = set()
    for score, chunk in scored:
        if chunk.page_number in seen_pages:
            continue
        section: Section = sections[chunk.section_ordinal]
        evidence.append(Evidence(chunk.page_number, section.heading, chunk.content[:900], round(score, 3)))
        seen_pages.add(chunk.page_number)
        if len(evidence) == limit:
            break
    return evidence


def answer(question: str, extract: FilingExtract, minimum_score: float = 0.6) -> Answer:
    evidence = retrieve(question, extract)
    if not evidence or evidence[0].score < minimum_score:
        return Answer("not_found", "Not found in this filing.", [])
    # Generation is deliberately deferred to the configured model; never fabricate an answer locally.
    return Answer("evidence_ready", "Evidence found. Answer generation is pending the configured AI provider.", evidence)
