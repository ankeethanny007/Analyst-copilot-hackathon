"""Evidence-bounded OpenAI answer generation."""

from __future__ import annotations

import os
import json
import re
import ssl
import time
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi


ABSTENTION = "Not found in this filing."
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
# The normal transport timeout remains suitable for embeddings and ingestion.
# Answer generation sets a shorter, request-scoped timeout inside its bounded
# retry loop so one stalled provider request cannot freeze the chat for several
# minutes.
_ANSWER_TIMEOUT_OVERRIDE: ContextVar[float | None] = ContextVar("answer_timeout_override", default=None)


@dataclass
class RetrievedEvidence:
    chunk_id: str | None
    section_id: str
    page_number: int
    heading: str
    excerpt: str
    score: float
    # The first six fields are intentionally compatible with the original
    # retrieval contract.  The remaining source snapshot fields make a table
    # or Inline XBRL fact citeable after the chat history is reloaded.
    source_type: str = "section"
    source_anchor: str | None = None
    table_id: str | None = None
    table_title: str | None = None


@dataclass(frozen=True)
class AnswerResult:
    content: str
    status: str
    citation_indices: tuple[int, ...]


def embed_question(question: str) -> list[float]:
    return embed_texts([question])[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    payload = _openai_post("/embeddings", {"model": model, "input": texts, "dimensions": 1536})
    return [item["embedding"] for item in payload["data"]]


def _citation_indices(content: str, source_count: int) -> tuple[int, ...]:
    values = tuple(dict.fromkeys(int(value) for value in re.findall(r"\[S(\d+)\]", content)))
    if not values or any(value < 1 or value > source_count for value in values):
        return ()
    return values


def _result(content: str, status: str, evidence: list[RetrievedEvidence]) -> AnswerResult:
    citations = _citation_indices(content, len(evidence)) if status == "supported" else ()
    if status == "supported" and not citations:
        return AnswerResult(ABSTENTION, "not_found", ())
    return AnswerResult(content, status, citations)


def generate_answer_result(question: str, evidence: list[RetrievedEvidence]) -> AnswerResult:
    """Generate only a cited answer bounded by the supplied evidence.

    A response is never marked supported merely because a model returned text:
    it must cite one or more supplied source labels.  Number/calculation
    provenance is enforced by the prompt and deterministic paths; citation
    validation is the final server-side guard before a message is persisted.
    """
    if not evidence:
        return AnswerResult(ABSTENTION, "not_found", ())
    from .question_planning import plan_question

    plan = plan_question(question)
    growth = _direct_growth_answer(question, evidence)
    if growth:
        return _result(growth, "supported", evidence)
    # A question asking what caused or drove a result must explain the
    # supported management discussion, not seize the first nearby numeric
    # table value. Direct extraction remains a fast, exact path only for a
    # direct metric question.
    if plan.intent == "direct":
        direct = _direct_metric_answer(question, evidence)
        if direct:
            return _result(direct, "supported", evidence)
    sources = "\n\n".join(
        f"[S{i + 1}: Page {item.page_number} · {item.heading}]\n{item.excerpt}"
        for i, item in enumerate(evidence)
    )
    instructions = (
        "You are an evidence-first financial filing assistant. Answer only from the supplied source excerpts. "
        f"If they do not support a direct answer, reply exactly: {ABSTENTION} "
        "Do not use outside knowledge or invent numbers, dates, units, calculations, or citations. "
        "Match the FinanceBench-style answer format: for information extraction, give the exact requested value or fact in the first sentence and cite it as [S#]. Cite every factual sentence with one or more [S#] labels. "
        "For numerical or logical reasoning (including growth, margins, or changes), give a one-sentence conclusion followed by a `Calculation:` line that states only the inputs and arithmetic supported by sources. "
        "For drivers of a change, identify only the causes explicitly discussed in the supplied management discussion or notes, and cite each source. "
        "For a list or filing-purpose question, return every responsive item disclosed in the supplied excerpt. If a requested period has no disclosed item in those sources, state that no item is listed for that period instead of discarding the supported items. "
        "For an analytical judgment framed as `based on` filing data (for example, capital intensity), make a clearly labelled inference from disclosed values and a simple stated calculation; do not present that inference as a quoted management conclusion. "
        "Lead with the metric or conclusion label (never a bare number). Preserve the requested unit: label a turnover or other multiple with `x`, and label percentages with `%`. Keep the response under 180 words and use at most three bullets after the conclusion or calculation. "
        "Except for the list/filing-purpose rule above, reply exactly `Not found in this filing.` if the supplied excerpts do not contain every requested fact, period, unit, and calculation input needed for the answer; for a driver question, abstain unless they contain an explicit management explanation. "
        "If an excerpt contains an explicit requested metric or driver under a neutral heading such as `Filing section`, it is still valid filing evidence: answer from it rather than abstaining. "
        "Do not restate the question, do not quote a source label that was not supplied, and do not claim a requested period or unit unless it appears in a source."
    )
    try:
        response = _answer_with_retry(
            {
                "model": os.getenv("OPENAI_MODEL", "gpt-5"),
                "instructions": instructions,
                "input": f"Question: {question}\n\nSources:\n{sources}",
            }
        )
    except Exception:
        # Distinguish an unavailable answer service from an evidence-backed
        # abstention.  The UI can offer a retry without incorrectly teaching
        # the user that the filing lacks the answer.
        return AnswerResult("The evidence review could not be completed. Please try again.", "failed", ())
    # The SDK exposes `output_text` as a convenience property, but the raw
    # REST response returned by urllib carries text inside output/message
    # content blocks.  Reading only the SDK field made every non-deterministic
    # answer look empty and therefore become a false "not found".
    text = _response_text(response)
    if not text or text == ABSTENTION:
        return AnswerResult(ABSTENTION, "not_found", ())
    return _result(text, "supported", evidence)


def _response_text(response: dict) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts: list[str] = []
    for output in response.get("output") or []:
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                parts.append(content["text"].strip())
    return "\n".join(part for part in parts if part).strip()


def _answer_with_retry(payload: dict) -> dict:
    """Retry a bounded number of transient answer-service failures.

    Retrieval has already produced document-scoped evidence by this point. A
    temporary provider failure should not be presented as a filing-level
    absence of evidence, and a short retry is much less disruptive than
    requiring the user to resend a long analytical question.
    """
    def setting(name: str, default: int) -> int:
        try:
            return max(1, int(os.getenv(name, str(default))))
        except ValueError:
            return default

    # Keep a transient retry, but bound the total time spent on one visible
    # chat turn.  Previously every exception could trigger three 90-second
    # requests, which looked like a hung UI and retried permanent 401/400
    # configuration failures as well.
    try:
        retries = max(0, int(os.getenv("ANSWER_MAX_RETRIES", "1")))
    except ValueError:
        retries = 1
    max_total_seconds = setting("ANSWER_MAX_TOTAL_SECONDS", 100)
    request_timeout_seconds = setting("ANSWER_REQUEST_TIMEOUT_SECONDS", 60)
    deadline = time.monotonic() + max_total_seconds

    def retryable(error: Exception) -> bool:
        if isinstance(error, HTTPError):
            return error.code in {408, 409, 425, 429} or 500 <= error.code < 600
        if isinstance(error, (URLError, TimeoutError, ConnectionError)):
            return True
        # This preserves retry coverage for the temporary transport wrapper
        # used in tests without retrying permanent configuration/model errors.
        return isinstance(error, RuntimeError) and bool(re.search(r"\b(?:temporary|transient|timeout|connection)\b", str(error), re.I))

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        timeout_token = _ANSWER_TIMEOUT_OVERRIDE.set(min(float(request_timeout_seconds), remaining))
        try:
            return _openai_post("/responses", payload)
        except Exception as error:  # caller maps a final failure to a retryable UI state
            last_error = error
            if attempt >= retries or not retryable(error):
                break
            pause = min(float(2**attempt), 4.0, max(0.0, deadline - time.monotonic()))
            if pause <= 0:
                break
            time.sleep(pause)
        finally:
            _ANSWER_TIMEOUT_OVERRIDE.reset(timeout_token)
    assert last_error is not None
    raise last_error


def generate_answer(question: str, evidence: list[RetrievedEvidence]) -> tuple[str, str]:
    """Compatibility wrapper used by direct unit tests and local scripts."""
    result = generate_answer_result(question, evidence)
    return result.content, result.status


def _direct_metric_answer(question: str, evidence: list[RetrievedEvidence]) -> str | None:
    """Return an exact reported metric when the question and source align.

    This deterministic path prevents a generative abstention from hiding an
    unambiguous value already present in the filing evidence.
    """
    # Do not treat a time phrase such as "three months ended March" as a
    # metric.  The former implementation did exactly that and could return a
    # nearby year/date fragment as a confident financial answer.
    from .question_planning import is_requested_statement_heading, plan_question

    plan = plan_question(question)
    candidates = [
        phrase
        for phrase in (plan.answer_phrases or plan.phrases)
        if len(phrase) >= 5
        and phrase not in {"cash flow", "cash flows", "balance sheet", "income statement", "statement of income", "statement of operations"}
        and not all(word in {"three", "months", "march", "ended", "year"} for word in phrase.split())
    ]
    # When the planner found a precise multi-word financial label, do not let
    # a generic single token (for example `net`) capture an unrelated line
    # earlier in the same balance sheet.
    if any(len(phrase.split()) >= 2 for phrase in candidates):
        candidates = [phrase for phrase in candidates if len(phrase.split()) >= 2]
    # `PP&E` on its own refers to the gross balance-sheet line in many
    # filings.  When the user explicitly requests net PP&E, only a candidate
    # that retains that qualifier is eligible to answer; otherwise an exact
    # generic match can return gross PP&E with false confidence.
    if re.search(r"\bnet\s+pp(?:&e|e|ne)\b", question, re.I):
        candidates = [phrase for phrase in candidates if "net" in phrase.lower()]
    if not candidates:
        words = [word for word in re.findall(r"[a-zA-Z]{3,}", question.lower()) if word not in {"three", "months", "march", "ended", "year", "answer", "using", "shown"}]
        candidates = [" ".join(words[index:index + size]) for size in range(min(4, len(words)), 1, -1) for index in range(len(words) - size + 1)]
    requested_years = list(plan.years)

    def source_unit(text: str) -> str | None:
        lowered = text.lower()
        if re.search(r"\b(?:dollars?\s+)?in\s+billions\b", lowered):
            return "billion"
        if re.search(r"\b(?:dollars?\s+)?in\s+millions\b", lowered):
            return "million"
        if re.search(r"\b(?:dollars?\s+)?in\s+thousands\b", lowered):
            return "thousand"
        return None

    def requested_unit(text: str) -> str | None:
        lowered = text.lower()
        if re.search(r"\b(?:usd|us\$|dollars?)?\s*(?:in\s+)?billions?\b", lowered):
            return "billion"
        if re.search(r"\b(?:usd|us\$|dollars?)?\s*(?:in\s+)?millions?\b", lowered):
            return "million"
        if re.search(r"\b(?:usd|us\$|dollars?)?\s*(?:in\s+)?thousands?\b", lowered):
            return "thousand"
        return None

    def display_value(value: str, source: str | None, requested: str | None) -> tuple[str, str]:
        if not source or not requested or source == requested:
            return value, source or requested or ""
        factors = {"thousand": Decimal("0.001"), "million": Decimal(1), "billion": Decimal(1000)}
        try:
            converted = Decimal(value.replace(",", "")) * factors[source] / factors[requested]
        except (InvalidOperation, KeyError):
            return value, source
        # Converted values are analyst-facing units; retain two decimals to
        # avoid hiding useful scale while preserving a stable result format.
        rendered = f"{converted.quantize(Decimal('0.01')):,.2f}"
        return rendered, requested

    def amounts(value: str) -> list[str]:
        return [
            match.group(1)
            for match in re.finditer(r"(?<![\w,])\$?\s*([\d][\d,]*(?:\.\d+)?)(?![\dA-Za-z])", value)
            if not re.fullmatch(r"(?:19|20)\d{2}", match.group(1).replace(",", ""))
        ]

    def header_years(lines: list[str], line_index: int) -> list[str]:
        # Check the nearby header first.  SEC tables usually place the dates
        # directly above the line item, and values then follow the same order.
        for offset in range(line_index, max(-1, line_index - 6), -1):
            header = re.findall(r"\b(?:19|20)\d{2}\b", lines[offset])
            if len(header) >= 2:
                return header
        # Extracted HTML tables can render each header cell on its own line,
        # and a requested metric may be dozens of rows below that header.
        # Find the last annual-period heading before the metric and collect
        # its consecutive year cells rather than defaulting to column one.
        candidates: list[list[str]] = []
        for offset, line in enumerate(lines[:line_index]):
            if not re.search(r"\b(?:years? ended|as of|december 31)\b", line, re.I):
                continue
            years: list[str] = []
            for header_line in lines[offset : min(line_index, offset + 10)]:
                found = re.findall(r"\b(?:19|20)\d{2}\b", header_line)
                if found:
                    years.extend(found)
                elif years:
                    break
            if len(years) >= 2:
                candidates.append(list(dict.fromkeys(years)))
        if candidates:
            return candidates[-1]
        return []

    def contains_requested_metric(item: RetrievedEvidence) -> bool:
        return any(
            re.search(rf"\b{re.escape(metric)}\b", item.excerpt, re.IGNORECASE)
            for metric in candidates
        )

    # Evidence is numbered in stable page order for the UI, but a formal
    # consolidated statement may occur later than an overview table. If the
    # user explicitly requested a statement and that statement contains the
    # requested metric, make it the only eligible direct-metric source. This
    # avoids citing an MD&A/non-GAAP recap merely because it repeats the same
    # number. Inline XBRL facts are also eligible when their mapped section
    # heading is the requested formal statement.
    indexed_evidence = list(enumerate(evidence, start=1))
    formal_statement_sources = [
        pair
        for pair in indexed_evidence
        if pair[1].source_type in {"table", "xbrl"}
        and is_requested_statement_heading(pair[1].heading, plan.statement_hint)
    ]
    statement_sources = [pair for pair in formal_statement_sources if contains_requested_metric(pair[1])]
    # Some benchmarks explicitly define absence from an enumerated financial
    # statement as zero.  This is safe only when the user requests that exact
    # fallback and a formal requested statement was retrieved; otherwise an
    # unmatched metric must continue through the evidence-first answer path.
    explicit_zero_if_absent = bool(
        re.search(r"\bif\b[^.?!]{0,100}\bnot\b[^.?!]{0,60}\b(?:state|report|answer)\s+(?:it\s+(?:as|is)\s+)?0\b", question, re.I)
    )
    if explicit_zero_if_absent and formal_statement_sources and not statement_sources:
        source_index = formal_statement_sources[0][0]
        label = candidates[0] if candidates else "requested costs"
        return f"Answer: {label[0].upper() + label[1:]} explicitly presented in the requested statement were $0. [S{source_index}]"
    eligible_evidence = statement_sources or indexed_evidence
    ordered_evidence = sorted(
        eligible_evidence,
        key=lambda pair: (pair[1].score, pair[1].source_type == "table"),
        reverse=True,
    )
    for item_index, item in ordered_evidence:
        lines = [line.strip() for line in item.excerpt.splitlines() if line.strip()] or [" ".join(item.excerpt.split())]
        for line_index, line in enumerate(lines):
            for metric in candidates:
                match = re.search(rf"\b({re.escape(metric)})\b", line, re.IGNORECASE)
                if not match:
                    continue
                values = amounts(line[match.end() :])
                if not values:
                    # Some compact table renderings lose newlines. Fall back
                    # to rows after the matched metric only. ``match.end()``
                    # is relative to this line, not the full excerpt; applying
                    # it to ``item.excerpt`` starts near the document header
                    # and can silently return an unrelated earlier value.
                    values = amounts(" ".join(lines[line_index + 1 :]))
                if not values:
                    continue
                value = values[0]
                years = header_years(lines, line_index)
                if requested_years and years and requested_years[-1] in years and len(values) >= len(years):
                    value = values[years.index(requested_years[-1])]
                label = match.group(1)
                lowered_question = question.lower()
                if "capital expenditure" in lowered_question or re.search(r"\bcapex\b", lowered_question):
                    label = "Capital expenditure"
                elif "net ppne" in lowered_question or "net pp&e" in lowered_question:
                    label = "Net PP&E"
                value, unit_label = display_value(value, source_unit(item.excerpt), requested_unit(question))
                unit = f" {unit_label}" if unit_label else ""
                return f"Answer: {label[0].upper() + label[1:]} was ${value}{unit}. [S{item_index}]"
    return None


def _direct_growth_answer(question: str, evidence: list[RetrievedEvidence]) -> str | None:
    """Calculate a year-over-year change only from a single cited table."""
    from .question_planning import is_requested_statement_heading, plan_question

    plan = plan_question(question)
    lowered_question = " ".join(question.lower().split())
    metric_aliases = {
        "gross revenue": ("net sales", "revenue", "revenues"),
        "total revenue": ("net sales", "revenue", "revenues"),
        "net sales": ("net sales",),
        "revenue": ("net sales", "revenue", "revenues"),
        "gross profit": ("gross profit",),
    }
    requested_metric: str | None = None
    for metric in metric_aliases:
        escaped = re.escape(metric)
        if re.search(rf"\b(?:growth|increase|decrease|decline|change)\s+(?:in|of)\s+(?:\w+\s+){{0,2}}{escaped}\b", lowered_question):
            requested_metric = metric
            break
        if re.search(rf"\b{escaped}\s+(?:growth|increase|decrease|decline|change)\b", lowered_question):
            requested_metric = metric
            break
    # A formula can mention a change in one of its inputs (for example,
    # inventory inside a DPO calculation).  That must not route the whole
    # question through the revenue-growth shortcut.
    if requested_metric is None:
        return None
    years = sorted(set(re.findall(r"(?:FY\s*)?(20\d{2})\b", question, re.I)), reverse=True)
    if len(years) < 2:
        return None
    allowed_labels = metric_aliases[requested_metric]
    indexed_evidence = list(enumerate(evidence, start=1))
    statement_sources = [
        pair
        for pair in indexed_evidence
        if pair[1].source_type in {"table", "xbrl"}
        and is_requested_statement_heading(pair[1].heading, plan.statement_hint)
    ]
    ordered_evidence = sorted(
        statement_sources or indexed_evidence,
        key=lambda pair: (pair[1].score, pair[1].source_type == "table"),
        reverse=True,
    )
    for index, item in ordered_evidence:
        text = " ".join(item.excerpt.split())
        if not all(year in text for year in years):
            continue
        labels = "|".join(re.escape(label) for label in allowed_labels)
        match = re.search(rf"\b({labels})\b", text, re.I)
        if not match:
            continue
        values = [
            int(value.replace(",", ""))
            for value in re.findall(r"(?<![\w,])\$?\s*([\d][\d,]*)(?![\dA-Za-z])", text[match.end() :])
            if not re.fullmatch(r"(?:19|20)\d{2}", value.replace(",", ""))
        ]
        if len(values) < 2:
            continue
        # SEC tables often contain three to five annual columns.  Map the
        # requested periods to the nearest header immediately before the
        # metric instead of assuming the first two cells are the requested
        # years.  This is critical for selected-financial-data tables ordered
        # oldest-to-newest.
        header_years = re.findall(r"\b(?:19|20)\d{2}\b", text[: match.start()])
        header_years = header_years[-min(len(header_years), len(values)) :]
        year_values = dict(zip(header_years, values[: len(header_years)]))
        if years[0] in year_values and years[1] in year_values:
            latest, prior = year_values[years[0]], year_values[years[1]]
        elif len(values) == 2:
            latest, prior = values
        else:
            continue
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
    timeout = _ANSWER_TIMEOUT_OVERRIDE.get() or 90
    with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        return json.loads(response.read())
