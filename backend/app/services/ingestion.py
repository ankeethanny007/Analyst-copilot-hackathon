"""Small, provider-neutral Day 1 primitives for durable filing ingestion."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
import re

from fastapi import UploadFile
from pypdf import PdfReader


@dataclass(frozen=True)
class FilingIdentity:
    filing_type: str | None = None
    fiscal_year: str | None = None
    fiscal_period: str | None = None
    period_end: str | None = None

    def label(self) -> str:
        parts: list[str] = []
        if self.fiscal_year:
            parts.append(f"FY{self.fiscal_year}")
        if self.fiscal_period and self.fiscal_period != "FY":
            parts.append(self.fiscal_period)
        if self.filing_type:
            parts.append(f"Form {self.filing_type}")
        label = " ".join(parts) or "an identifiable SEC filing"
        if self.period_end:
            label += f" (period ended {self.period_end})"
        return label


def expected_identity_from_filename(filename: str) -> FilingIdentity | None:
    """Read an explicit year/quarter/form convention from a filing filename."""
    normalized = re.sub(r"[^A-Z0-9]+", "_", Path(filename).stem.upper()).strip("_")
    form_match = re.search(r"(?:^|_)(10Q|10K|8K)(?:_|$)", normalized)
    if not form_match:
        return None
    filing_type = {"10Q": "10-Q", "10K": "10-K", "8K": "8-K"}[form_match.group(1)]
    period_matches = list(re.finditer(r"((?:19|20)\d{2})(?:_?(Q[1-4]|FY))?", normalized[: form_match.start()]))
    fiscal_year = period_matches[-1].group(1) if period_matches else None
    fiscal_period = period_matches[-1].group(2) if period_matches else None
    if filing_type == "10-K" and fiscal_year:
        fiscal_period = "FY"
    return FilingIdentity(filing_type, fiscal_year, fiscal_period)


def _clean_inline_value(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _inline_xbrl_value(source: str, concept: str) -> str | None:
    match = re.search(
        rf"<(?:ix:)?(?:nonNumeric|nonFraction)\b[^>]*\bname\s*=\s*['\"][^'\"]*{re.escape(concept)}['\"][^>]*>(.*?)</(?:ix:)?(?:nonNumeric|nonFraction)\s*>",
        source,
        re.I | re.S,
    )
    return _clean_inline_value(match.group(1)) if match else None


def _period_from_date(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    cleaned = " ".join(value.replace(".", "").split())
    parsed: datetime | None = None
    for pattern in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(cleaned, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        return None, value
    quarter = f"Q{((parsed.month - 1) // 3) + 1}"
    return quarter, parsed.strftime("%B %-d, %Y")


def identity_from_html(source: str) -> FilingIdentity | None:
    filing_type = _inline_xbrl_value(source, "DocumentType")
    fiscal_year = _inline_xbrl_value(source, "DocumentFiscalYearFocus")
    fiscal_period = _inline_xbrl_value(source, "DocumentFiscalPeriodFocus")
    period_end_raw = _inline_xbrl_value(source, "DocumentPeriodEndDate")
    inferred_period, period_end = _period_from_date(period_end_raw)
    if filing_type:
        filing_type = filing_type.upper().replace("FORM ", "").strip()
    if not fiscal_period and filing_type == "10-Q":
        fiscal_period = inferred_period
    if not fiscal_year and period_end:
        fiscal_year = re.search(r"\b((?:19|20)\d{2})\b", period_end).group(1) if re.search(r"\b((?:19|20)\d{2})\b", period_end) else None
    if not any((filing_type, fiscal_year, fiscal_period, period_end)):
        return None
    return FilingIdentity(filing_type, fiscal_year, fiscal_period, period_end)


def _identity_from_pdf(upload: UploadFile) -> FilingIdentity | None:
    try:
        reader = PdfReader(upload.file)
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:3])
    except Exception:
        return None
    form_match = re.search(r"\bFORM\s+(10-[QK]|8-K)\b", text, re.I)
    date_match = re.search(
        r"(?:quarterly period|three months|six months|nine months|fiscal year|year)\s+ended\s+([A-Z][a-z]+\s+\d{1,2},\s+(?:19|20)\d{2})",
        text,
        re.I,
    )
    filing_type = form_match.group(1).upper() if form_match else None
    inferred_period, period_end = _period_from_date(date_match.group(1) if date_match else None)
    fiscal_year_match = re.search(r"\b((?:19|20)\d{2})\b", period_end or "")
    fiscal_year = fiscal_year_match.group(1) if fiscal_year_match else None
    fiscal_period = "FY" if filing_type == "10-K" else inferred_period if filing_type == "10-Q" else None
    return FilingIdentity(filing_type, fiscal_year, fiscal_period, period_end) if any((filing_type, fiscal_year, fiscal_period)) else None


def filing_identity_mismatch(filename: str, expected: FilingIdentity | None, actual: FilingIdentity | None) -> str | None:
    """Return a user-facing mismatch only for identity fields known on both sides."""
    if expected is None or actual is None:
        return None
    comparisons = (
        (expected.filing_type, actual.filing_type),
        (expected.fiscal_year, actual.fiscal_year),
        (expected.fiscal_period, actual.fiscal_period),
    )
    if not any(wanted and found and wanted != found for wanted, found in comparisons):
        return None
    return (
        f"Incorrect file. Based on the filename “{filename}”, expected {expected.label()}, "
        f"but the file contains {actual.label()} instead. Upload the expected filing or rename the file to match its contents."
    )


async def validate_upload_identity(upload: UploadFile, media_type: str, max_html_bytes: int = 4 * 1024 * 1024) -> str | None:
    """Compare explicit filename identity with filing metadata, then rewind."""
    expected = expected_identity_from_filename(upload.filename or "")
    if expected is None:
        return None
    try:
        if media_type == "application/pdf":
            actual = _identity_from_pdf(upload)
        else:
            source = (await upload.read(max_html_bytes)).decode("utf-8", errors="ignore")
            actual = identity_from_html(source)
    finally:
        await upload.seek(0)
    return filing_identity_mismatch(upload.filename or "filing", expected, actual)


async def sha256_upload(upload: UploadFile, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """Hash an upload without holding a filing in memory, then rewind it for R2."""
    digest = hashlib.sha256()
    size_bytes = 0
    while chunk := await upload.read(chunk_size):
        digest.update(chunk)
        size_bytes += len(chunk)
    await upload.seek(0)
    return digest.hexdigest(), size_bytes
