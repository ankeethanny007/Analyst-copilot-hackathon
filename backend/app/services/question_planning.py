"""Deterministic question planning for filing retrieval.

The planner deliberately does not answer questions.  It turns an analyst's
plain-English question into the small set of concepts, periods and document
areas used to retrieve evidence from the active filing.  Keeping this step
deterministic makes the evidence boundary visible and gives the LLM a much
better source set than a single embedding search alone.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


STOP_WORDS = {
    "a", "about", "an", "and", "answer", "as", "at", "based", "be", "by",
    "company", "details", "do", "does", "ended", "for", "from", "give", "has",
    "have", "how", "if", "in", "information", "is", "it", "its", "of", "on",
    "or", "primarily", "please", "public", "question", "relying", "shown", "state",
    "the", "their", "this", "to", "using", "was", "were", "what", "when", "which",
    "who", "with", "you", "your",
}


# Analyst wording often differs from the label in a US filing.  These are
# retrieval expansions, not answer substitutions: the answer still has to be
# supported by the source text and says what the filing actually calls it.
ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "revenues", "net sales", "sales", "total net revenue"),
    "gross revenue": ("revenue", "revenues", "net sales", "sales"),
    "capex": ("capital expenditures", "capital expenditure", "purchases of property", "property plant and equipment"),
    "capital expenditure": ("capital expenditures", "purchases of property", "property plant and equipment"),
    "pp&e": ("property plant and equipment", "property, plant and equipment", "fixed assets"),
    "ppe": ("property plant and equipment", "property, plant and equipment", "fixed assets"),
    # Keep the `net` qualifier in every expansion.  A generic PP&E expansion
    # can otherwise match the gross PP&E line immediately above the net line
    # on a balance sheet and yield a confident but wrong value.
    "net pp&e": (
        "property, plant and equipment — net",
        "property, plant and equipment - net",
        "property, plant and equipment net",
        "net property, plant and equipment",
        "net property plant and equipment",
        "fixed assets net",
    ),
    "net ppe": (
        "property, plant and equipment — net",
        "property, plant and equipment - net",
        "property, plant and equipment net",
        "net property, plant and equipment",
        "net property plant and equipment",
        "fixed assets net",
    ),
    "net ppne": (
        "property, plant and equipment — net",
        "property, plant and equipment - net",
        "property, plant and equipment net",
        "net property, plant and equipment",
        "net property plant and equipment",
        "fixed assets net",
    ),
    "profit": ("net income", "net earnings", "income", "earnings", "profit"),
    "margin": ("margin", "gross profit", "operating income", "operating margin"),
    "cash flow": ("cash flows", "operating activities", "cash from operations"),
    "shareholder": ("beneficial ownership", "principal shareholders", "stockholders", "shareholders"),
    "stakeholder": ("beneficial ownership", "principal shareholders", "stockholders", "shareholders"),
    "holder": ("beneficial ownership", "principal shareholders", "stockholders", "shareholders"),
    "debt": ("debt", "notes", "borrowings", "long-term debt"),
    "liquidity": ("liquidity", "cash and cash equivalents", "current assets", "current liabilities"),
}


METRIC_PATTERNS = (
    "total net revenue",
    "net revenue",
    "total revenue",
    "net sales",
    "gross profit",
    "gross margin",
    "operating income",
    "operating margin",
    "net income",
    "net earnings",
    "cash from operations",
    "operating cash flow",
    "capital expenditures",
    "capital expenditure",
    "property, plant and equipment",
    "property plant and equipment",
    "total assets",
    "total current liabilities",
    "current assets",
    "quick ratio",
    "fixed asset turnover",
    "return on assets",
)


@dataclass(frozen=True)
class QuestionPlan:
    question: str
    terms: tuple[str, ...]
    phrases: tuple[str, ...]
    answer_phrases: tuple[str, ...]
    years: tuple[str, ...]
    statement_hint: str | None
    intent: str
    needs_calculation: bool


def _tokens(question: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9&]+", question.lower()) if len(token) > 1 and token not in STOP_WORDS and not token.isdigit()]


def _statement_hint(question: str) -> str | None:
    lowered = question.lower()
    if any(value in lowered for value in ("cash flow", "cash flows", "cash-flow")):
        return "cash flow"
    if any(value in lowered for value in ("balance sheet", "financial position", "statement of financial position")):
        return "balance sheet"
    if any(value in lowered for value in ("income statement", "statement of operations", "statement of income", "profit and loss", "p&l")):
        return "income statement"
    if any(value in lowered for value in ("management discussion", "mda", "md&a")):
        return "management discussion"
    return None


def _intent(question: str) -> tuple[str, bool]:
    lowered = question.lower()
    calculation = bool(re.search(r"\b(ratio|margin|growth|increase|decrease|decline|change|average|turnover|calculate|calculation|percent|percentage)\b", lowered))
    if re.search(r"\b(why|what drove|what led|reason for|drivers? of|caused?)\b", lowered):
        return "driver", calculation
    if re.search(r"\b(who|stakeholder|shareholder|beneficial owner|ownership)\b", lowered):
        return "ownership", calculation
    if calculation:
        return "calculation", True
    if re.search(r"\b(which|list|what securities|registered)\b", lowered):
        return "list", False
    return "direct", False


def plan_question(question: str) -> QuestionPlan:
    """Return retrieval features without calling a model or external service."""
    lowered = " ".join(question.lower().split())
    phrases: list[str] = []
    direct_metrics: list[str] = []
    for phrase in METRIC_PATTERNS:
        if phrase in lowered:
            phrases.append(phrase)
            direct_metrics.append(phrase)
    alias_phrases: list[tuple[str, tuple[str, ...]]] = []
    for phrase, expansions in ALIASES.items():
        if phrase in lowered:
            phrases.append(phrase)
            phrases.extend(expansions)
            alias_phrases.append((phrase, expansions))
    # Preserve ordered de-duplication; phrase order is useful when choosing a
    # focused excerpt around the first requested metric.
    phrases = list(dict.fromkeys(phrases))
    terms = _tokens(question)
    for phrase in phrases:
        terms.extend(_tokens(phrase))
    intent, needs_calculation = _intent(question)
    structural_context = {"cash flow", "cash flows", "balance sheet", "income statement", "statement of income", "statement of operations"}
    specific_metrics = [phrase for phrase in direct_metrics if phrase not in structural_context]
    specific_aliases = [(phrase, expansions) for phrase, expansions in alias_phrases if phrase not in structural_context]
    # If a question names an actual financial metric and also says which
    # statement to use, direct extraction must follow the metric—not a label
    # such as "cash flows from operating activities" that happens to appear
    # first in the statement.
    selected_metrics = specific_metrics or direct_metrics
    selected_aliases = specific_aliases or alias_phrases
    answer_phrases = list(selected_metrics)
    for phrase, expansions in selected_aliases:
        answer_phrases.append(phrase)
        answer_phrases.extend(expansions)
    return QuestionPlan(
        question=question,
        terms=tuple(dict.fromkeys(terms)),
        phrases=tuple(phrases),
        answer_phrases=tuple(dict.fromkeys(answer_phrases)),
        # Finance questions commonly write FY2022 without a separator, so a
        # plain word-boundary year regex would silently lose the period.
        years=tuple(dict.fromkeys(re.findall(r"(?:FY\s*)?((?:19|20)\d{2})\b", question, re.I))),
        statement_hint=_statement_hint(question),
        intent=intent,
        needs_calculation=needs_calculation,
    )
