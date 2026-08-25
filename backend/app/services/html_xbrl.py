"""HTML/Inline XBRL-first filing normalization with stable evidence locations."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Tag

PAGE_BREAK = re.compile(r"page-break-after\s*:\s*always", re.I)
HEADING = re.compile(r"^(item\s+\d+[a-z]?\.?|[A-Z][A-Z\s,&—–()\-]{8,})$", re.I)
FINANCIAL_STATEMENT = re.compile(r"consolidated\s+(?:statements?\s+of\s+(?:cash\s+flows?|income|operations)|balance\s+sheets?)", re.I)


@dataclass
class Page:
    number: int
    anchor: str
    content: str


@dataclass
class Section:
    page_number: int
    ordinal: int
    heading: str
    content: str
    source_anchor: str


@dataclass
class Table:
    page_number: int
    title: str | None
    rows: list[list[str]]
    source_anchor: str


@dataclass
class XbrlFact:
    concept: str
    value: str
    context_ref: str | None
    unit: str | None
    decimals: str | None
    period_start: str | None
    period_end: str | None
    instant_date: str | None
    page_number: int | None
    source_anchor: str | None


@dataclass
class Chunk:
    page_number: int
    section_ordinal: int
    content: str
    content_type: str


@dataclass
class FilingExtract:
    pages: list[Page]
    sections: list[Section]
    tables: list[Table]
    facts: list[XbrlFact]
    chunks: list[Chunk]

    def summary(self) -> dict[str, int]:
        return {"pages": len(self.pages), "sections": len(self.sections), "tables": len(self.tables), "facts": len(self.facts), "chunks": len(self.chunks)}

    def to_dict(self) -> dict:
        return {name: [asdict(item) for item in getattr(self, name)] for name in ("pages", "sections", "tables", "facts", "chunks")}


def normalized_text(node: Tag | BeautifulSoup) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _parts_after_page_breaks(body: Tag) -> Iterable[str]:
    """Split both SEC conventions: styled `<hr>` and styled empty paragraphs."""
    marker = "__ANALYST_COPILOT_PAGE_BREAK__"
    for node in body.find_all(lambda tag: isinstance(tag, Tag) and PAGE_BREAK.search(tag.get("style", ""))):
        node.insert_after(marker)
    return (part for part in body.decode_contents().split(marker))


def _page_heading(soup: BeautifulSoup, fallback: str) -> str:
    # SEC HTML repeats a "Table of Contents" label on many rendered pages.
    # Prefer the actual financial-statement title when it appears anywhere on
    # the page, so source links communicate useful evidence to the analyst.
    statement = FINANCIAL_STATEMENT.search(normalized_text(soup))
    if statement:
        return statement.group(0).title().replace(" Of ", " of ")
    for node in soup.find_all(["h1", "h2", "h3", "h4", "b", "strong", "span", "div"]):
        text = normalized_text(node)
        if text.lower() == "table of contents":
            continue
        if 4 <= len(text) <= 120 and HEADING.match(text):
            return text
    return fallback


def _context_dates(soup: BeautifulSoup) -> dict[str, tuple[str | None, str | None, str | None]]:
    contexts: dict[str, tuple[str | None, str | None, str | None]] = {}
    for context in soup.find_all(lambda tag: isinstance(tag, Tag) and tag.name.endswith(":context")):
        period = context.find(lambda tag: isinstance(tag, Tag) and tag.name.endswith(":period"))
        if not period or not context.get("id"):
            continue
        value = lambda suffix: normalized_text(period.find(lambda tag: isinstance(tag, Tag) and tag.name.endswith(suffix))) if period.find(lambda tag: isinstance(tag, Tag) and tag.name.endswith(suffix)) else None
        contexts[context["id"]] = (value(":startdate"), value(":enddate"), value(":instant"))
    return contexts


def _extract_facts(soup: BeautifulSoup, contexts: dict[str, tuple[str | None, str | None, str | None]], fact_pages: dict[str, int]) -> list[XbrlFact]:
    facts: list[XbrlFact] = []
    for node in soup.find_all(lambda tag: isinstance(tag, Tag) and tag.name in {"ix:nonfraction", "ix:nonnumeric", "ix:fraction"}):
        if node.find_parent(lambda parent: isinstance(parent, Tag) and parent.name == "ix:header"):
            continue
        context_ref = node.get("contextref")
        start, end, instant = contexts.get(context_ref, (None, None, None))
        anchor = node.get("id")
        facts.append(XbrlFact(node.get("name", "unknown"), normalized_text(node), context_ref, node.get("unitref"), node.get("decimals"), start, end, instant, fact_pages.get(anchor), anchor))
    return facts


def _extract_tables(page: BeautifulSoup, page_number: int) -> list[Table]:
    tables: list[Table] = []
    for index, table in enumerate(page.find_all("table"), start=1):
        rows = [[normalized_text(cell) for cell in row.find_all(["th", "td"])] for row in table.find_all("tr")]
        rows = [row for row in rows if any(row)]
        if rows:
            tables.append(Table(page_number, rows[0][0] if rows[0] else None, rows, f"page-{page_number}-table-{index}"))
    return tables


def _chunk(content: str, page_number: int, section_ordinal: int, size: int = 1200, overlap: int = 180) -> list[Chunk]:
    chunks: list[Chunk] = []
    for start in range(0, len(content), size - overlap):
        excerpt = content[start : start + size].strip()
        if excerpt:
            chunks.append(Chunk(page_number, section_ordinal, excerpt, "narrative"))
        if start + size >= len(content):
            break
    return chunks


def parse_html_xbrl(path: Path) -> FilingExtract:
    """Parse a filing locally; persistence and embedding are deliberately separate."""
    source = path.read_text(encoding="utf-8", errors="ignore")
    root = BeautifulSoup(source, "html.parser")
    body = root.body or root
    header = body.find("ix:header")
    if header:
        header.decompose()
    page_html = list(_parts_after_page_breaks(body))
    fact_pages: dict[str, int] = {}
    for page_number, html in enumerate(page_html, start=1):
        page = BeautifulSoup(html, "html.parser")
        for fact in page.find_all(lambda tag: isinstance(tag, Tag) and tag.name in {"ix:nonfraction", "ix:nonnumeric", "ix:fraction"}):
            if fact.get("id"):
                fact_pages[fact["id"]] = page_number
    contexts = _context_dates(root)
    facts = _extract_facts(root, contexts, fact_pages)

    pages: list[Page] = []
    sections: list[Section] = []
    tables: list[Table] = []
    chunks: list[Chunk] = []
    for page_number, html in enumerate(page_html, start=1):
        page = BeautifulSoup(html, "html.parser")
        content = normalized_text(page)
        if not content:
            continue
        anchor = f"page-{page_number}"
        heading = _page_heading(page, f"Page {page_number}")
        pages.append(Page(page_number, anchor, content))
        ordinal = len(sections) + 1
        section = Section(page_number, ordinal, heading, content, anchor)
        sections.append(section)
        chunks.extend(_chunk(content, page_number, ordinal))
        tables.extend(_extract_tables(page, page_number))
        for table in tables:
            if table.page_number == page_number:
                rendered = "\n".join(" | ".join(row) for row in table.rows)
                table_chunks = _chunk(rendered, page_number, ordinal, 1000, 120)
                for chunk in table_chunks:
                    chunk.content_type = "table"
                chunks.extend(table_chunks)
    return FilingExtract(pages, sections, tables, facts, chunks)
