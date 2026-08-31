"""Hybrid, document-scoped evidence ranking for filing questions.

Vector search is useful for paraphrases but unreliable on its own for financial
tables, periods and exact line items.  These helpers merge semantic matches
with lexical section scoring, statement-table scoring and persisted Inline
XBRL facts.  The caller supplies rows *already scoped to one document*.
"""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from .answering import RetrievedEvidence
from .filing_retrieval import render_table, table_heading
from .question_planning import QuestionPlan, is_requested_statement_heading
from .source_labels import display_source_heading, is_real_table_of_contents, is_real_table_of_contents_table


SOURCE_ORDER = {"table": 0, "xbrl": 1, "section": 2}
_GENERIC_PERIOD_TABLE_HEADING = re.compile(
    r"^(?:(?:fiscal\s+)?years?|(?:three|six|nine|twelve)\s+months?)\s+ended\b",
    re.I,
)


def _normalise(value: str) -> str:
    return " ".join(value.lower().split())


def _is_generic_period_table_heading(heading: str) -> bool:
    """Return whether a table title only describes its reporting period.

    Formal statements sometimes persist their real title as the enclosing
    section heading while the table itself begins with a period row such as
    ``Fiscal Years Ended``.  That period row must not hide the statement
    identity during evidence ranking.
    """
    return bool(_GENERIC_PERIOD_TABLE_HEADING.match(_normalise(heading)))


def _concept_words(concept: str) -> str:
    tail = concept.split(":")[-1]
    tail = re.sub(r"([a-z])([A-Z])", r"\1 \2", tail)
    return tail.replace("_", " ").replace("-", " ")


def _excerpt(content: str, plan: QuestionPlan, limit: int = 1500) -> str:
    """Return a bounded excerpt centred on the strongest requested term."""
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    lowered = compact.lower()
    positions = [lowered.find(value.lower()) for value in (*plan.phrases, *plan.terms) if value and lowered.find(value.lower()) >= 0]
    start = max(0, min(positions) - limit // 4) if positions else 0
    end = min(len(compact), start + limit)
    if end < len(compact):
        cut = compact.rfind(" ", start, end)
        end = cut if cut > start else end
    prefix = "…" if start else ""
    suffix = "…" if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def _table_excerpt(content: str, plan: QuestionPlan, limit: int = 2100) -> str:
    """Keep financial-statement rows intact for human-readable citations.

    ``render_table`` uses one line per source row.  Collapsing that structure
    into ordinary prose makes an otherwise precise source difficult to audit
    in the evidence popup.  For small tables retain every row; for long ones,
    retain the header and a short neighbourhood around the best matching row.
    """
    rows = [" | ".join(cell.strip() for cell in row.split("|") if cell.strip()) for row in content.splitlines()]
    rows = [row for row in rows if row]
    rendered = "\n".join(rows)
    if len(rendered) <= limit:
        return rendered

    def searchable(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.lower()))

    def focused_row(candidates: Iterable[str]) -> int | None:
        normalised_candidates = [searchable(value) for value in candidates if searchable(value)]
        for index, row in enumerate(rows):
            haystack = searchable(row)
            if any(candidate in haystack for candidate in normalised_candidates):
                return index
        return None

    # An explicit answer phrase (including a filing-label alias such as
    # "purchases of property") is more specific than a statement name such as
    # "cash flows".  Long cash-flow statements otherwise retained their first
    # generic operating-cash-flow row and discarded the requested capex row.
    specific = sorted(
        dict.fromkeys(plan.answer_phrases),
        key=lambda value: (len(searchable(value).split()), len(searchable(value))),
        reverse=True,
    )
    focus = focused_row(specific)
    if focus is None:
        focus = focused_row((*plan.phrases, *plan.terms))
    if focus is None:
        focus = 0
    # The first rows commonly carry the statement title, dates and units.
    selected = set(range(min(3, len(rows))))
    selected.update(range(max(0, focus - 1), min(len(rows), focus + 2)))
    ordered = [row for index, row in enumerate(rows) if index in selected]
    excerpt = "\n".join(ordered)
    if len(excerpt) <= limit:
        return excerpt
    return _excerpt(excerpt, plan, limit=limit)


def _term_score(text: str, heading: str, plan: QuestionPlan) -> float:
    haystack = _normalise(text)
    title = _normalise(heading)
    score = 0.0
    for phrase in plan.phrases:
        phrase = _normalise(phrase)
        # A three-word line item such as "total net revenue" is much more
        # diagnostic than a generic alias such as "revenue".  Count the
        # requested phrase once with a specificity weight instead of letting
        # repeated generic words in another table dominate the ranking.
        phrase_weight = 3.0 + 6.0 * max(0, len(phrase.split()) - 1)
        if phrase and phrase in haystack:
            score += phrase_weight
        if phrase and phrase in title:
            score += phrase_weight + 4.0
    for term in plan.terms:
        if term in haystack:
            score += 1.2
        if term in title:
            score += 3.0
    for year in plan.years:
        if year in haystack:
            score += 2.0
    if plan.statement_hint and plan.statement_hint in haystack:
        score += 8.0
    if plan.intent == "driver" and re.search(r"\b(due to|driven by|primarily|because|result of|impact of)\b", haystack):
        score += 3.5
    if plan.intent == "ownership" and re.search(r"\b(beneficial ownership|principal shareholders|stockholders)\b", haystack):
        score += 8.0
    if "table of contents" in title:
        score -= 25.0
    return score


def _semantic_scores(matches: Iterable[dict[str, Any]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for row in matches:
        section_id = row.get("section_id")
        if not section_id:
            continue
        try:
            similarity = float(row.get("similarity", 0.0))
        except (TypeError, ValueError):
            continue
        # Do not use a hard threshold.  A semantic candidate still needs
        # lexical/table evidence to beat unrelated content, but is never
        # discarded solely because embedding scores vary by filing.
        scores[section_id] = max(scores.get(section_id, 0.0), max(0.0, similarity) * 8.0)
    return scores


def section_evidence(plan: QuestionPlan, sections: Iterable[dict[str, Any]], semantic_matches: Iterable[dict[str, Any]], limit: int = 4) -> list[RetrievedEvidence]:
    semantic = _semantic_scores(semantic_matches)
    candidates: list[RetrievedEvidence] = []
    for section in sections:
        section_id = section.get("id")
        content = section.get("content") or ""
        if not section_id or not content:
            continue
        raw_heading = section.get("heading") or ""
        # A split EDGAR navigation link (``T able of Contents``) appears on
        # many substantive pages.  Suppress only pages whose body actually
        # has a navigational contents-list structure.
        if is_real_table_of_contents(content):
            continue
        heading = display_source_heading(raw_heading)
        score = _term_score(content, heading, plan) + semantic.get(section_id, 0.0)
        if score <= 0:
            continue
        candidates.append(
            RetrievedEvidence(
                None,
                section_id,
                int(section.get("page_number") or 0),
                heading,
                _excerpt(content, plan),
                score,
                source_type="section",
                source_anchor=section.get("source_anchor"),
            )
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]


def table_evidence(
    plan: QuestionPlan,
    tables: Iterable[dict[str, Any]],
    *,
    section_by_id: dict[str, dict[str, Any]] | None = None,
    limit: int = 4,
) -> list[RetrievedEvidence]:
    candidates: list[RetrievedEvidence] = []
    for table in tables:
        section_id = table.get("section_id")
        if not section_id:
            # A table without a page/section relationship is not safe enough
            # to cite in a document-grounded response.
            continue
        rendered = render_table(table)
        if not rendered:
            continue
        if is_real_table_of_contents_table(table.get("title"), rendered):
            # A contents page is useful for navigation, never as financial
            # evidence.  Its broad vocabulary otherwise creates false hits.
            continue
        heading = table_heading(table, rendered)
        section = (section_by_id or {}).get(section_id, {})
        associated_heading = display_source_heading(section.get("heading"), "")
        formal_statement = is_requested_statement_heading(
            heading,
            plan.statement_hint,
            associated_heading,
        )
        if formal_statement and _is_generic_period_table_heading(heading) and associated_heading:
            # Use the section's actual financial-statement label for both the
            # source UI and the answer layer's formal-statement eligibility
            # guard. The table label alone may contain only a period heading.
            heading = associated_heading
        score = _term_score(rendered, heading, plan)
        for phrase in plan.phrases:
            if len(phrase.split()) >= 2 and re.search(rf"(?:^|\n)\s*{re.escape(phrase)}\b", rendered, re.I):
                # A financial-statement row that exactly matches the metric
                # is stronger evidence than a discussion/table that merely
                # mentions the same words somewhere else.
                score += 18.0
        heading_lower = heading.lower()
        if "consolidated" in heading_lower:
            # Prefer the firm-wide statement/financial highlights over a
            # business-line table when the question did not name a segment.
            score += 18.0
        if formal_statement:
            # A question that expressly asks for a financial statement should
            # draw its cited value from that formal statement, not from an
            # MD&A recap or a non-GAAP reconciliation that happens to repeat
            # the same metric.  The answer layer applies the corresponding
            # eligibility guard once the statement also contains the metric.
            score += 42.0
        if plan.intent == "direct" and re.search(r"\b(segment|markets|international|consumer|commercial|corporate)\b", heading_lower):
            score -= 8.0
        if plan.statement_hint and plan.statement_hint in _normalise(rendered):
            score += 4.0
        if re.search(r"\b(?:19|20)\d{2}\b", rendered) and plan.years:
            score += 3.0
        if score <= 0:
            continue
        candidates.append(
            RetrievedEvidence(
                None,
                section_id,
                int(table.get("page_number") or 0),
                heading,
                _table_excerpt(rendered, plan, limit=2100),
                score + 3.0,  # Structured rows are especially valuable for numeric questions.
                source_type="table",
                source_anchor=table.get("source_anchor"),
                table_id=table.get("id"),
                # Snapshot the resolved display title instead of the raw
                # extraction label.  This also prevents a historical
                # ``T able of Contents`` title from reappearing on reload.
                table_title=heading,
            )
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]


def fact_evidence(plan: QuestionPlan, facts: Iterable[dict[str, Any]], sections: dict[str, dict[str, Any]], limit: int = 3) -> list[RetrievedEvidence]:
    """Return citable Inline XBRL facts with a period/page/section binding."""
    candidates: list[RetrievedEvidence] = []
    for fact in facts:
        section_id = fact.get("section_id")
        section = sections.get(section_id or "")
        page_number = fact.get("page_number") or (section or {}).get("page_number")
        if not section_id or not section or not page_number:
            continue
        concept = _concept_words(str(fact.get("concept") or ""))
        period = fact.get("period_end") or fact.get("instant_date") or ""
        content = " | ".join(
            part for part in (
                f"Inline XBRL fact: {concept}",
                f"Value: {fact.get('normalized_value') or fact.get('value')}",
                f"Period: {period}" if period else "",
                f"Unit: {fact.get('unit')}" if fact.get("unit") else "",
            ) if part
        )
        score = _term_score(content, section.get("heading") or "", plan)
        if plan.years and any(year in str(period) for year in plan.years):
            score += 4.0
        if score <= 0:
            continue
        candidates.append(
            RetrievedEvidence(
                None,
                section_id,
                int(page_number),
                display_source_heading(section.get("heading"), f"Inline XBRL fact: {concept}"),
                content,
                score,
                source_type="xbrl",
                source_anchor=fact.get("source_anchor") or section.get("source_anchor"),
            )
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]


def rank_evidence(
    plan: QuestionPlan,
    *,
    sections: Iterable[dict[str, Any]],
    semantic_matches: Iterable[dict[str, Any]],
    tables: Iterable[dict[str, Any]],
    facts: Iterable[dict[str, Any]],
    limit: int = 7,
) -> list[RetrievedEvidence]:
    """Merge candidates, retain diversity, then put citations in page order."""
    section_rows = list(sections)
    section_map = {row["id"]: row for row in section_rows if row.get("id")}
    candidates = [
        *table_evidence(plan, tables, section_by_id=section_map),
        *section_evidence(plan, section_rows, semantic_matches),
        *fact_evidence(plan, facts, section_map),
    ]
    candidates.sort(key=lambda item: item.score, reverse=True)
    selected: list[RetrievedEvidence] = []
    seen: set[tuple[str, str, str]] = set()
    source_counts: dict[str, int] = {}
    for item in candidates:
        key = (item.source_type, item.section_id, item.excerpt[:100])
        if key in seen:
            continue
        # Do not let a long filing's repeated fact/table rows crowd out the
        # narrative needed to explain a driver or a calculation.
        if source_counts.get(item.source_type, 0) >= {"table": 3, "section": 3, "xbrl": 2}.get(item.source_type, 2):
            continue
        seen.add(key)
        source_counts[item.source_type] = source_counts.get(item.source_type, 0) + 1
        selected.append(item)
        if len(selected) >= limit:
            break
    return sorted(selected, key=lambda item: (item.page_number, SOURCE_ORDER.get(item.source_type, 9), -item.score))
