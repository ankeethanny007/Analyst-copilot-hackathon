"""Classification and display helpers for extracted source labels.

SEC HTML filings often place a ``Table of Contents`` navigation link at the
top of every rendered page.  Inline markup can split that link into strings
such as ``T able of Contents``.  A label alone is therefore not enough to
decide that a section is a real table of contents: the page must also contain
the characteristic list of navigational entries.
"""

from __future__ import annotations

import re


_WHITESPACE = re.compile(r"\s+")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
# ``get_text`` normally inserts spaces between inline tags, but some filings
# leave adjacent text nodes joined. Allow either form while retaining the
# usual split ``T able of Contents`` detection.
_TOC_HEADING = re.compile(r"\bt\s*able\s*of\s*contents\b", re.I)
_TOC_ENTRY = re.compile(
    r"\b(?:item\s+\d{1,2}[a-z]?|note\s+\d{1,2})\.?\s+[^.\n]{2,100}?\s+\d{1,3}\b",
    re.I,
)
_TOC_CROSS_REFERENCE = re.compile(
    r"\b(?:see|refer(?:red)?\s+to|described\s+in|discussed\s+in|included\s+in|provided\s+in)\s*$",
    re.I,
)


def clean_source_label(value: str | None) -> str:
    """Return a whitespace-normalized label without changing its wording."""
    return _WHITESPACE.sub(" ", value or "").strip()


def _compact(value: str | None) -> str:
    return _NON_ALPHANUMERIC.sub("", clean_source_label(value).lower())


def is_table_of_contents_label(value: str | None) -> bool:
    """Recognize ``Table of Contents`` even when inline tags split letters."""
    return _compact(value) == "tableofcontents"


def starts_with_table_of_contents(value: str | None) -> bool:
    """Return whether extracted text starts with a (possibly split) TOC label."""
    return _compact(value).startswith("tableofcontents")


def _contains_table_of_contents(value: str | None) -> bool:
    """Return whether text contains a normal or inline-split TOC heading."""
    return bool(_TOC_HEADING.search(clean_source_label(value)))


def _is_cross_reference(text: str, entry_start: int, entry: str) -> bool:
    """Reject prose such as ``See Note 13`` from TOC entry detection.

    Filing prose frequently tells the reader to see a financial-statement
    note.  That is not a navigation row, even when a nearby number happens to
    make it resemble one after HTML text has been flattened.
    """
    before = text[max(0, entry_start - 80) : entry_start]
    if _TOC_CROSS_REFERENCE.search(before):
        return True
    # ``Note 13 and Note 14`` can otherwise be parsed as a fake row whose
    # page number is ``14``. A contents title can naturally contain "Note",
    # but it should not finish with the next note marker.
    return bool(re.search(r"\b(?:item|note)\s*$", entry, re.I))


def _has_contents_structure(value: str | None) -> bool:
    """Detect the navigational list structure of a real table-of-contents page."""
    text = clean_source_label(value)
    entries = [
        match.group(0)
        for match in _TOC_ENTRY.finditer(text)
        if not _is_cross_reference(text, match.start(), match.group(0))
    ]
    # A TOC normally has several Item/Note entries.  ``Beginning Page`` is a
    # strong explicit signal, and permits a compact first TOC page with one
    # visible entry in a truncated extraction.
    return len(entries) >= 3 or (
        bool(re.search(r"\bbeginning\s+page\b", text, re.I)) and bool(entries)
    )


def is_real_table_of_contents(value: str | None) -> bool:
    """Return true only for a TOC heading *and* a TOC-like page body.

    This intentionally keeps substantive pages whose EDGAR navigation begins
    with ``T able of Contents``. Those pages have the heading but not a list
    of multiple page-index entries. The heading need not be the first text on
    the page: SEC cover matter sometimes precedes the actual contents page.
    """
    text = clean_source_label(value)
    return any(_has_contents_structure(text[match.end() :]) for match in _TOC_HEADING.finditer(text))


def is_real_table_of_contents_table(title: str | None, content: str | None) -> bool:
    """Classify a table whose TOC title may be stored separately from its rows."""
    if _has_contents_structure(content):
        # Parser cleanup can turn a page whose only prior label was the
        # navigation link into ``Page N``. Keep treating a structurally
        # contents-like table on that generic page as navigation.
        return bool(
            is_table_of_contents_label(title)
            or _contains_table_of_contents(content)
            or re.fullmatch(r"page\s+\d+", clean_source_label(title), re.I)
        )
    if not (is_table_of_contents_label(title) or _contains_table_of_contents(content)):
        return False
    # A short, non-financial table titled exactly as a TOC is navigation, not
    # evidence.  The financial signals avoid discarding a substantive table
    # that inherited a split navigation label from its page.
    compact = clean_source_label(content)
    has_financial_signal = bool(
        re.search(r"[$€£¥%]|\b(?:19|20)\d{2}\b|\b(?:millions?|thousands?|billions?|dollars?)\b", compact, re.I)
    )
    return is_table_of_contents_label(title) and len(compact) <= 400 and not has_financial_signal


def display_source_heading(value: str | None, fallback: str = "Filing section") -> str:
    """Return a user-facing source heading that never exposes a TOC artifact."""
    cleaned = clean_source_label(value)
    if not cleaned or is_table_of_contents_label(cleaned) or re.fullmatch(r"page\s+\d+", cleaned, re.I):
        return fallback
    return cleaned
