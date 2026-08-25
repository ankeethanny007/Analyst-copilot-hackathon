"""Text/page PDF fallback for filings that do not have usable SEC HTML.

This deliberately does not perform OCR.  A page with no extractable text is
recorded as unavailable instead of inventing evidence, which preserves the
MVP's evidence-first promise.
"""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from .html_xbrl import Chunk, FilingExtract, Page, Section


def _heading(text: str, page_number: int) -> str:
    for line in text.splitlines()[:12]:
        value = " ".join(line.split())
        if 4 <= len(value) <= 160 and not re.fullmatch(r"[\d,.$()%\-–— ]+", value):
            return value
    return f"Page {page_number}"


def _chunks(content: str, page_number: int, section_ordinal: int, size: int = 1200, overlap: int = 180) -> list[Chunk]:
    values: list[Chunk] = []
    for start in range(0, len(content), size - overlap):
        excerpt = content[start : start + size].strip()
        if excerpt:
            values.append(Chunk(page_number, section_ordinal, excerpt, "narrative"))
        if start + size >= len(content):
            break
    return values


def parse_pdf_fallback(path: Path) -> FilingExtract:
    """Extract page-addressable PDF text; table/XBRL extraction is unavailable."""
    reader = PdfReader(str(path))
    pages: list[Page] = []
    sections: list[Section] = []
    chunks: list[Chunk] = []
    for page_number, page in enumerate(reader.pages, start=1):
        content = "\n".join(line.strip() for line in (page.extract_text() or "").splitlines() if line.strip())
        if not content:
            continue
        anchor = f"page-{page_number}"
        ordinal = len(sections) + 1
        pages.append(Page(page_number, anchor, content))
        sections.append(Section(page_number, ordinal, _heading(content, page_number), content, anchor))
        chunks.extend(_chunks(content, page_number, ordinal))
    if not pages:
        raise RuntimeError("No extractable text was found in this PDF; upload its SEC HTML filing instead.")
    return FilingExtract(pages, sections, [], [], chunks)
