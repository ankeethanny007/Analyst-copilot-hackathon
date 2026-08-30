from backend.app.services.answering import generate_answer_result
from backend.app.services.hybrid_retrieval import _table_excerpt, rank_evidence
from backend.app.services.question_planning import plan_question


def test_direct_metric_prefers_a_consolidated_table_over_a_contents_mention() -> None:
    plan = plan_question("What was total net revenue for the three months ended March 31, 2022?")
    sections = [
        {"id": "toc", "page_number": 2, "heading": "Table of Contents", "content": "Consolidated statements of income and total net revenue", "source_anchor": "page-2"},
        {"id": "generic-toc", "page_number": 3, "heading": "Page 3", "content": "Table of Contents Consolidated statements of income and total net revenue", "source_anchor": "page-3"},
        {"id": "statement", "page_number": 80, "heading": "Consolidated statements of income", "content": "Total net revenue was reported in the statement.", "source_anchor": "page-80"},
    ]
    tables = [
        {"id": "toc-table", "section_id": "toc", "page_number": 2, "title": "Table of Contents", "content": {"rows": [["Table of Contents"], ["Total net revenue", "80"]]}, "source_anchor": "page-2-table-1"},
        {"id": "income-table", "section_id": "statement", "page_number": 80, "title": "Consolidated statements of income", "content": {"rows": [["Three months ended March 31 (in millions)", "2022", "2021"], ["Total net revenue", "$", "30,717", "$", "32,266"]]}, "source_anchor": "page-80-table-1"},
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=tables, facts=[])

    assert evidence[0].page_number == 80
    assert evidence[0].source_type == "table"
    assert all(item.page_number != 3 for item in evidence)
    assert "\n" in evidence[0].excerpt
    assert "Three months ended March 31" in evidence[0].excerpt
    assert "Total net revenue" in evidence[0].excerpt
    result = generate_answer_result(plan.question, evidence)
    assert result.status == "supported"
    assert "$30,717 million" in result.content
    assert result.citation_indices == (1,)


def test_xbrl_facts_are_retrieved_with_period_and_page_evidence() -> None:
    plan = plan_question("What was total revenue in 2022?")
    sections = [{"id": "income", "page_number": 40, "heading": "Consolidated statements of income", "content": "Income statement", "source_anchor": "page-40"}]
    facts = [
        {
            "id": "revenue-fact",
            "section_id": "income",
            "concept": "us-gaap:Revenues",
            "value": "30,717",
            "normalized_value": "30717000000",
            "unit": "usd",
            "period_end": "2022-03-31",
            "instant_date": None,
            "page_number": 40,
            "source_anchor": "revenue-fact",
        }
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=[], facts=facts)

    assert len(evidence) == 1
    assert evidence[0].source_type == "xbrl"
    assert evidence[0].page_number == 40
    assert "2022-03-31" in evidence[0].excerpt


def test_question_plan_identifies_period_statement_and_driver_intent() -> None:
    plan = plan_question("What drove operating margin change from FY2021 to FY2022 in the income statement?")

    assert plan.intent == "driver"
    assert plan.needs_calculation
    assert plan.statement_hint == "income statement"
    assert plan.years == ("2021", "2022")
    assert "operating margin" in plan.phrases


def test_long_statement_table_keeps_the_specific_requested_metric_row() -> None:
    plan = plan_question(
        "What is the FY2018 capital expenditure amount (in USD millions) for 3M? "
        "Give a response relying on the cash flow statement."
    )
    rows = [
        "Consolidated Statement of Cash Flows",
        "Years ended December 31 | 2018 | 2017 | 2016",
        "(Millions)",
        "Cash flows from operating activities | 6,439 | 6,240 | 6,662",
        *[f"Other cash flow line {index} | {'x' * 180}" for index in range(12)],
        "Purchases of property, plant and equipment (PP&E) | (1,577) | (1,373) | (1,420)",
        "Free cash flow | 4,862 | 4,867 | 5,242",
    ]

    excerpt = _table_excerpt("\n".join(rows), plan, limit=900)

    assert "Consolidated Statement of Cash Flows" in excerpt
    assert "Purchases of property, plant and equipment" in excerpt
    assert "Free cash flow" in excerpt
