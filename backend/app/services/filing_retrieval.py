"""Table-aware evidence selection for broad filing questions."""

from __future__ import annotations

import re
from typing import Any

from .source_labels import display_source_heading


ALIASES = {
    "revenue": ("revenue", "revenues", "sales", "net sales", "total sales"),
    "gross": ("gross profit", "gross margin", "cost of sales", "sales"),
    "profit": ("net income", "net earnings", "income", "earnings", "profit"),
    "growth": ("increase", "decrease", "change", "growth", "year-over-year"),
    "decline": ("decrease", "declined", "lower", "decreased", "down"),
    "stakeholder": ("beneficial ownership", "principal shareholders", "stockholders", "shareholders", "ownership"),
    "holder": ("beneficial ownership", "shareholders", "stockholders", "ownership"),
}


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z]{3,}", value.lower()) if term not in {"what", "were", "with", "from", "that", "this", "year", "please", "using", "shown", "following", "answer", "assume", "public", "analyst"}}


def render_table(table: dict[str, Any]) -> str:
    rows = table.get("content", {}).get("rows", [])
    return "\n".join(" | ".join(str(cell) for cell in row if cell) for row in rows if row).strip()


def table_heading(table: dict[str, Any], content: str) -> str:
    match = re.search(r"consolidated\s+(?:statements?\s+of\s+(?:cash\s+flows?|income|operations)|balance\s+sheets?)", content, re.I)
    if match:
        return match.group(0).title().replace(" Of ", " of ")
    return display_source_heading(table.get("title"), "Financial table")


def relevant_tables(question: str, tables: list[dict[str, Any]], limit: int = 3) -> list[tuple[dict[str, Any], str, int]]:
    """Return question-relevant tables, using aliases for common analyst phrasing."""
    terms = _terms(question)
    years = re.findall(r"\b20\d{2}\b", question)
    expanded = set(terms)
    for term in terms:
        expanded.update(ALIASES.get(term, ()))
    ranked: list[tuple[dict[str, Any], str, int]] = []
    for table in tables:
        content = render_table(table)
        haystack = content.lower()
        score = sum(3 if " " in term else 1 for term in expanded if term in haystack)
        if len(years) >= 2 and all(year in haystack for year in years):
            score += 12
        if "net sales" in haystack and any(term in terms for term in ("revenue", "gross", "growth")):
            score += 6
            sales_row = haystack.split("net sales", 1)[1][:180]
            if re.search(r"\d{1,3},\d{3}.*\d{1,3},\d{3}", sales_row):
                score += 20
            else:
                score -= 8
        if "consolidated" in haystack and any(term in expanded for term in ("revenue", "sales", "profit", "income", "gross")):
            score += 2
        if score:
            ranked.append((table, content, score))
    return sorted(ranked, key=lambda item: item[2], reverse=True)[:limit]
