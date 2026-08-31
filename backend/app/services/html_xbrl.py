"""HTML/Inline XBRL-first filing normalization with stable evidence locations."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Tag

from .source_labels import is_table_of_contents_label

PAGE_BREAK = re.compile(r"page-break-after\s*:\s*always", re.I)
HEADING = re.compile(r"^(item\s+\d+[a-z]?\.?|[A-Z][A-Z\s,&—–()\-]{8,})$", re.I)
FINANCIAL_STATEMENT = re.compile(
    r"consolidated\s+(?:statements?\s+of\s+(?:cash\s+flows?|income|operations|comprehensive\s+income|changes\s+in\s+(?:stockholders['’]?|shareholders['’]?)\s+equity)|balance\s+sheets?)",
    re.I,
)
INLINE_FACT_NAMES = {"ix:nonfraction", "ix:nonnumeric", "ix:fraction"}
FACT_KEY_ATTRIBUTE = "data-analyst-copilot-fact-key"


def _repair_display_label(value: str) -> str:
    """Repair common one-character word splits in SEC styled headings.

    Inline XBRL span boundaries occasionally make BeautifulSoup render labels
    such as ``Balance Shee t`` and ``Cash Flow s``.  Apply this only to display
    labels, never to the evidence body, so source text remains unmodified.
    """
    repairs = (
        (r"\bshee\s+t\b", "Sheet"),
        (r"\bflow\s+s\b", "Flows"),
        (r"\boperati\s+ons\b", "Operations"),
        (r"\bdat\s+a\b", "Data"),
    )
    repaired = value
    for pattern, replacement in repairs:
        repaired = re.sub(
            pattern,
            lambda match: replacement.upper() if match.group(0).isupper() else replacement,
            repaired,
            flags=re.I,
        )
    return re.sub(r"\s+", " ", repaired).strip()


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
    # Keep the human-rendered value above for source display. These extra
    # fields preserve the Inline XBRL numeric semantics for callers that need
    # a calculation-ready number without changing the persisted schema yet.
    normalized_value: str | None = None
    scale: str | None = None
    sign: str | None = None


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


def _local_name(tag: Tag) -> str:
    """Return a namespace-agnostic, lower-cased tag name."""
    return str(tag.name or "").rsplit(":", 1)[-1].lower()


def _is_inline_fact(tag: Tag) -> bool:
    return str(tag.name or "").lower() in INLINE_FACT_NAMES


def _parts_after_page_breaks(body: Tag) -> Iterable[str]:
    """Split both SEC conventions: styled `<hr>` and styled empty paragraphs."""
    marker = "__ANALYST_COPILOT_PAGE_BREAK__"
    for node in body.find_all(lambda tag: isinstance(tag, Tag) and PAGE_BREAK.search(tag.get("style", ""))):
        node.insert_after(marker)
    return (part for part in body.decode_contents().split(marker))


def _page_heading(soup: BeautifulSoup, fallback: str) -> str:
    # A contents table can mention every statement in a filing. Looking for a
    # statement title in all page text therefore labels the TOC as a financial
    # statement. Restrict this pass to short, non-table title elements.
    for node in soup.find_all(["h1", "h2", "h3", "h4", "b", "strong", "span", "div"]):
        if node.find_parent("table") or node.find("table"):
            continue
        text = _repair_display_label(normalized_text(node))
        # EDGAR places this navigation link at the start of many substantive
        # pages.  Its letters can be split by inline tags (``T able``), so an
        # exact string comparison would incorrectly make it the page heading.
        if is_table_of_contents_label(text):
            continue
        title = text.rstrip(":").strip()
        colon_heading = (
            text.endswith(":")
            and 4 <= len(title) <= 100
            and len(title.split()) <= 12
            and bool(re.fullmatch(r"[A-Za-z0-9&,'’()/\-\s]+", title))
        )
        if 4 <= len(text) <= 180 and FINANCIAL_STATEMENT.search(text):
            return text
        if 4 <= len(text) <= 120 and (HEADING.match(text) or colon_heading):
            return title if colon_heading else text
    return fallback


def _context_dates(soup: BeautifulSoup) -> dict[str, tuple[str | None, str | None, str | None]]:
    contexts: dict[str, tuple[str | None, str | None, str | None]] = {}
    for context in soup.find_all(lambda tag: isinstance(tag, Tag) and _local_name(tag) == "context"):
        period = context.find(lambda tag: isinstance(tag, Tag) and _local_name(tag) == "period")
        if not period or not context.get("id"):
            continue
        value = lambda name: normalized_text(match) if (match := period.find(lambda tag: isinstance(tag, Tag) and _local_name(tag) == name)) else None
        contexts[context["id"]] = (value("startdate"), value("enddate"), value("instant"))
    return contexts


def _decimal_from_text(value: str, transform: str | None = None) -> Decimal | None:
    """Normalize common Inline XBRL numeric displays without guessing words/dates."""
    raw = value.replace("\u2212", "-").replace("\u2013", "-").replace("\xa0", " ").strip()
    if not raw:
        return None
    if (transform or "").lower().endswith("fixed-zero"):
        return Decimal(0)

    compact = re.sub(r"\s+", "", raw)
    negative_parentheses = compact.startswith("(") and compact.endswith(")")
    if negative_parentheses:
        compact = compact[1:-1]
    compact = compact.replace("$", "").replace("€", "").replace("£", "").replace("¥", "").replace("%", "")
    transform_name = (transform or "").lower()
    if "comma-decimal" in transform_name:
        compact = compact.replace(".", "").replace(",", ".")
    else:
        compact = compact.replace(",", "")

    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", compact):
        return None
    try:
        parsed = Decimal(compact)
    except InvalidOperation:
        return None
    return -abs(parsed) if negative_parentheses else parsed


def _decimal_string(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _normalized_fact_value(node: Tag) -> str | None:
    if _local_name(node) == "nonnumeric":
        return None

    if _local_name(node) == "fraction":
        numerator = node.find(lambda tag: isinstance(tag, Tag) and _local_name(tag) == "numerator")
        denominator = node.find(lambda tag: isinstance(tag, Tag) and _local_name(tag) == "denominator")
        if not numerator or not denominator:
            return None
        numerator_value = _decimal_from_text(normalized_text(numerator), numerator.get("format"))
        denominator_value = _decimal_from_text(normalized_text(denominator), denominator.get("format"))
        if numerator_value is None or denominator_value in {None, Decimal(0)}:
            return None
        value = numerator_value / denominator_value
    else:
        value = _decimal_from_text(normalized_text(node), node.get("format"))

    if value is None:
        return None
    try:
        scale = int(str(node.get("scale", "0")))
    except (TypeError, ValueError):
        return None
    value *= Decimal(10) ** scale
    if node.get("sign") == "-":
        value = -abs(value)
    return _decimal_string(value)


def _extract_facts(soup: BeautifulSoup, contexts: dict[str, tuple[str | None, str | None, str | None]], fact_pages: dict[str, int]) -> list[XbrlFact]:
    facts: list[XbrlFact] = []
    for node in soup.find_all(lambda tag: isinstance(tag, Tag) and _is_inline_fact(tag)):
        if node.find_parent(lambda parent: isinstance(parent, Tag) and parent.name == "ix:header"):
            continue
        context_ref = node.get("contextref")
        start, end, instant = contexts.get(context_ref, (None, None, None))
        fact_key = node.get(FACT_KEY_ATTRIBUTE)
        anchor = node.get("id") or fact_key
        facts.append(
            XbrlFact(
                node.get("name", "unknown"),
                normalized_text(node),
                context_ref,
                node.get("unitref"),
                node.get("decimals"),
                start,
                end,
                instant,
                fact_pages.get(fact_key) or fact_pages.get(anchor),
                anchor,
                _normalized_fact_value(node),
                node.get("scale"),
                node.get("sign"),
            )
        )
    return facts


def _is_generic_table_title(title: str | None) -> bool:
    value = (title or "").strip().lower()
    if not value or is_table_of_contents_label(value) or value.startswith(("part i", "part ii", "page")):
        return True
    return bool(re.fullmatch(r"(?:\(?in [^)]+\)?|(?:as of |three months ended |year ended ).*)", value))


def _table_heading_before(table: Tag) -> str | None:
    """Find a nearby visible title without accidentally using another table's cells."""
    checked = 0
    for node in table.previous_elements:
        if not isinstance(node, Tag):
            continue
        checked += 1
        if checked > 180:
            break
        if node.find_parent("table") or node.find("table"):
            continue
        text = _repair_display_label(normalized_text(node))
        if not text or len(text) > 180 or is_table_of_contents_label(text):
            continue
        title = text.rstrip(":").strip()
        colon_heading = (
            text.endswith(":")
            and 4 <= len(title) <= 100
            and len(title.split()) <= 12
            and bool(re.fullmatch(r"[A-Za-z0-9&,'’()/\-\s]+", title))
        )
        if FINANCIAL_STATEMENT.search(text) or HEADING.match(text) or re.match(r"^(?:note|item)\s+\d+", text, re.I) or colon_heading:
            return title if colon_heading else text
    return None


def _extract_tables(page: BeautifulSoup, page_number: int, page_heading: str | None = None) -> list[Table]:
    tables: list[Table] = []
    for index, table in enumerate(page.find_all("table"), start=1):
        rows = [[normalized_text(cell) for cell in row.find_all(["th", "td"])] for row in table.find_all("tr")]
        rows = [row for row in rows if any(row)]
        if rows:
            row_title = _repair_display_label(rows[0][0]) if rows[0] else None
            nearby_heading = _table_heading_before(table)
            title = row_title
            if _is_generic_table_title(row_title):
                title = nearby_heading or page_heading
            elif nearby_heading and FINANCIAL_STATEMENT.search(nearby_heading) and nearby_heading.lower() not in row_title.lower():
                title = f"{nearby_heading} — {row_title}"
            tables.append(Table(page_number, _repair_display_label(title) if title else None, rows, f"page-{page_number}-table-{index}"))
    return tables


def _mark_facts_for_page_mapping(body: Tag) -> None:
    """Add an in-memory key so facts without an HTML id remain page-addressable."""
    for index, fact in enumerate(body.find_all(lambda tag: isinstance(tag, Tag) and _is_inline_fact(tag)), start=1):
        fact[FACT_KEY_ATTRIBUTE] = f"fact-{index}"


def _filing_body(root: BeautifulSoup) -> Tag | BeautifulSoup:
    """Select the actual filing from a multi-document SEC HTML download.

    Some EDGAR downloads concatenate the filing index, an interactive-data
    viewer, the submitted Inline XBRL document, and exhibits. BeautifulSoup's
    ``root.body`` returns only the first index body, which contains filenames
    but not the reported event. Prefer the concise body that carries an Inline
    XBRL ``DocumentType`` fact; ordinary single-document filings retain their
    only body unchanged.
    """
    bodies = root.find_all("body")
    if len(bodies) <= 1:
        return bodies[0] if bodies else root

    filing_candidates: list[Tag] = []
    for body in bodies:
        document_type = body.find(
            lambda tag: isinstance(tag, Tag)
            and _is_inline_fact(tag)
            and str(tag.get("name") or "").lower().endswith("documenttype")
        )
        if document_type:
            filing_candidates.append(body)
    if filing_candidates:
        # The interactive viewer can duplicate the same facts alongside its
        # controls. The submitted filing body is the concise candidate.
        return min(filing_candidates, key=lambda body: len(normalized_text(body)))
    return bodies[0]


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
    body = _filing_body(root)
    # Inline XBRL contexts normally live inside ix:header. Capture them before
    # removing that non-rendered header from the page text.
    contexts = _context_dates(body)
    header = body.find("ix:header")
    if header:
        header.decompose()
    _mark_facts_for_page_mapping(body)
    page_html = list(_parts_after_page_breaks(body))
    fact_pages: dict[str, int] = {}
    for page_number, html in enumerate(page_html, start=1):
        page = BeautifulSoup(html, "html.parser")
        for fact in page.find_all(lambda tag: isinstance(tag, Tag) and _is_inline_fact(tag)):
            if fact.get(FACT_KEY_ATTRIBUTE):
                fact_pages[fact[FACT_KEY_ATTRIBUTE]] = page_number
            elif fact.get("id"):
                # Preserve the old id-based mapping for callers passing in a
                # pre-split document without our in-memory fact marker.
                fact_pages[fact["id"]] = page_number
    facts = _extract_facts(body, contexts, fact_pages)

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
        tables.extend(_extract_tables(page, page_number, heading))
        for table in tables:
            if table.page_number == page_number:
                rendered = "\n".join(" | ".join(row) for row in table.rows)
                table_chunks = _chunk(rendered, page_number, ordinal, 1000, 120)
                for chunk in table_chunks:
                    chunk.content_type = "table"
                chunks.extend(table_chunks)
    return FilingExtract(pages, sections, tables, facts, chunks)
