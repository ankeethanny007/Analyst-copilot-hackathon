from backend.app.services.answering import RetrievedEvidence, generate_answer_result
from backend.app.services.hybrid_retrieval import _table_excerpt, rank_evidence
from backend.app.services.question_planning import is_requested_statement_heading, plan_question


def test_direct_metric_prefers_a_consolidated_table_over_a_contents_mention() -> None:
    plan = plan_question("What was total net revenue for the three months ended March 31, 2022?")
    sections = [
        {"id": "toc", "page_number": 2, "heading": "Table of Contents", "content": "Table of Contents Beginning Page Item 1 Business 4 Item 2 Properties 16 Item 3 Legal Proceedings 17", "source_anchor": "page-2"},
        {"id": "generic-toc", "page_number": 3, "heading": "Page 3", "content": "Table of Contents Item 5 Market for Equity Securities 20 Item 6 Selected Financial Data 24 Item 7 Management Discussion 29", "source_anchor": "page-3"},
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


def test_income_statement_matching_accepts_statements_of_earnings() -> None:
    plan = plan_question("What was revenue in FY2017 according to the statement of earnings?")

    assert plan.statement_hint == "income statement"
    assert is_requested_statement_heading(
        "Consolidated Statements of Earnings",
        plan.statement_hint,
    )
    assert not is_requested_statement_heading(
        "The following table presents effects on our Consolidated Statements of Earnings",
        plan.statement_hint,
    )


def test_capital_intensity_question_plans_for_disclosed_inference_inputs() -> None:
    plan = plan_question("Is the company a capital-intensive business based on FY2022 data?")

    assert plan.intent == "calculation"
    assert plan.needs_calculation
    assert "depreciation and amortization" in plan.phrases
    assert "property plant and equipment" in plan.phrases
    assert "net sales" in plan.phrases


def test_cash_flow_ratio_and_conversion_questions_plan_for_statement_inputs() -> None:
    liquidity = plan_question(
        "What is the FY2017 operating cash flow ratio? It is cash from operations divided by total current liabilities."
    )
    conversion = plan_question("Does the company have improving free cashflow conversion as of FY2022?")

    assert liquidity.intent == "calculation"
    assert "cash from operations" in liquidity.phrases
    assert "total current liabilities" in liquidity.phrases
    assert conversion.intent == "calculation"
    assert "purchases of property plant and equipment" in conversion.phrases
    assert "net income" in conversion.phrases


def test_acquisition_question_prioritizes_the_acquisition_note_over_generic_year_matches() -> None:
    plan = plan_question("What major acquisitions were completed in FY2023, FY2022 and FY2021?")
    sections = [
        {
            "id": "generic-years",
            "page_number": 27,
            "heading": "Shareholder Return Performance",
            "content": "Shareholder returns for 2023, 2022 and 2021 are presented below.",
            "source_anchor": "page-27",
        },
        {
            "id": "acquisitions",
            "page_number": 64,
            "heading": "Note 5 - Acquisitions and Divestitures",
            "content": (
                "Year ended June 30, 2023 Acquisitions. The Company completed the acquisition "
                "of a flexible packaging manufacturer and a medical device packaging site."
            ),
            "source_anchor": "page-64",
        },
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=[], facts=[], limit=1)

    assert len(evidence) == 1
    assert evidence[0].page_number == 64
    assert "acquisitions and divestitures" in plan.phrases
    assert plan.intent == "list"


def test_dpo_question_retrieves_both_balance_sheet_and_income_statement_inputs() -> None:
    plan = plan_question(
        "What is FY2017 days payable outstanding (DPO)? Calculate using average accounts payable, "
        "FY2017 COGS, and the change in inventory between FY2016 and FY2017 using the balance sheet "
        "and the P&L statement."
    )
    sections = [
        {"id": "balance", "page_number": 40, "heading": "Consolidated Balance Sheets"},
        {"id": "income", "page_number": 38, "heading": "Consolidated Statements of Operations"},
    ]
    tables = [
        {
            "id": "balance-table",
            "section_id": "balance",
            "page_number": 40,
            "title": "Consolidated Balance Sheets",
            "content": {"rows": [["2016", "2017"], ["Inventories", "11,461", "16,047"], ["Accounts payable", "25,309", "34,616"]]},
            "source_anchor": "page-40-table-1",
        },
        {
            "id": "income-table",
            "section_id": "income",
            "page_number": 38,
            "title": "Consolidated Statements of Operations",
            "content": {"rows": [["2016", "2017"], ["Cost of sales", "88,265", "111,934"]]},
            "source_anchor": "page-38-table-1",
        },
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=tables, facts=[], limit=6)

    assert plan.intent == "calculation"
    assert plan.statement_hint is None
    assert "accounts payable" in plan.phrases
    assert "cost of sales" in plan.phrases
    assert {item.page_number for item in evidence if item.source_type == "table"} == {38, 40}


def test_filing_agenda_question_uses_narrative_answering_path() -> None:
    plan = plan_question("What was the key agenda of the 8-K filing?")

    assert plan.intent == "list"
    assert "supplemental indenture" in plan.phrases
    assert "substitute issuer" in plan.phrases


def test_list_question_retains_all_items_from_a_long_narrative_note() -> None:
    plan = plan_question("What major acquisitions were completed in FY2023?")
    content = (
        "Note 5 - Acquisitions and Divestitures. First acquisition: Czech packaging plant. "
        + ("Transaction detail and purchase accounting. " * 45)
        + "Second acquisition: Shanghai medical packaging site. "
        + ("Additional transaction detail. " * 25)
        + "Third acquisition: New Zealand protein packaging machinery manufacturer."
    )
    sections = [
        {
            "id": "acquisition-note",
            "page_number": 64,
            "heading": "Acquisitions",
            "content": content,
            "source_anchor": "page-64",
        }
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=[], facts=[], limit=1)

    assert len(evidence[0].excerpt) > 1500
    assert "Third acquisition" in evidence[0].excerpt


def test_filing_purpose_excerpt_retains_operating_purpose_after_long_definitions() -> None:
    plan = plan_question("What was the key agenda of this 8-K filing?")
    content = (
        "Item 8.01 Other Events. The Former Issuer and Substitute Issuer entered into "
        "supplemental indentures with the trustee. "
        + ("Defined notes, parties, dates and governing indenture details. " * 65)
        + "The supplemental indentures relate to the substitution of the Substitute Issuer "
        "for the Former Issuer and assumption of the Former Issuer's covenants."
    )
    sections = [
        {
            "id": "other-events",
            "page_number": 1,
            "heading": "Item 8.01 Other Events",
            "content": content,
            "source_anchor": "page-1",
        }
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=[], facts=[], limit=1)

    assert "relate to the substitution" in evidence[0].excerpt
    assert "assumption of the Former Issuer's covenants" in evidence[0].excerpt


def test_excluding_m_and_a_retrieves_the_organic_segment_sales_bridge() -> None:
    plan = plan_question("Excluding the impact of M&A, which segment dragged down growth in 2022?")
    sections = [
        {
            "id": "generic-growth",
            "page_number": 20,
            "heading": "Results",
            "content": "Total company growth declined in 2022.",
        },
        {
            "id": "segment-bridge",
            "page_number": 25,
            "heading": "Worldwide Sales Change By Business Segment",
            "content": (
                "Organic sales Acquisitions Divestitures Translation Total sales change "
                "Safety and Industrial 1.0% 0% 0% (4.2)% (3.2)% "
                "Consumer (0.9)% 0% (0.4)% (2.6)% (3.9)%"
            ),
        },
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=[], facts=[], limit=1)

    assert "organic sales" in plan.phrases
    assert evidence[0].page_number == 25


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


def test_statement_qualified_metric_cites_the_formal_statement_not_a_non_gaap_recap() -> None:
    question = (
        "What is the FY2018 capital expenditure amount (in USD millions)? "
        "Give a response relying on the cash flow statement."
    )
    # The UI keeps evidence in page order. Deliberately give the earlier
    # non-GAAP recap a stronger score to prove that formal-statement
    # eligibility, not an accidental score advantage, controls the citation.
    evidence = [
        RetrievedEvidence(
            None,
            "page-49",
            49,
            "Free Cash Flow (non-GAAP measure)",
            "Years ended December 31 | 2018 | 2017\n(Millions)\n"
            "Purchases of property, plant and equipment | (1,577) | (1,373)",
            999.0,
            source_type="table",
        ),
        RetrievedEvidence(
            None,
            "page-60",
            60,
            "Consolidated Statements of Cash Flows",
            "Years ended December 31 | 2018 | 2017\n(Millions)\n"
            "Purchases of property, plant and equipment | (1,577) | (1,373)",
            1.0,
            source_type="table",
        ),
    ]

    result = generate_answer_result(question, evidence)

    assert result.status == "supported"
    assert "$1,577 million" in result.content
    assert result.citation_indices == (2,)


def test_formal_requested_statement_table_outranks_a_non_gaap_recap() -> None:
    plan = plan_question(
        "What is the FY2018 capital expenditure amount (in USD millions)? "
        "Give a response relying on the cash flow statement."
    )
    tables = [
        {
            "id": "recap",
            "section_id": "page-49",
            "page_number": 49,
            "title": "Free Cash Flow (non-GAAP measure)",
            "content": {
                "rows": [
                    ["Cash flow", "cash flow", "cash flow", "cash flow"],
                    ["Capital expenditures", "(1,577)"],
                ]
            },
            "source_anchor": "page-49-table-1",
        },
        {
            "id": "statement",
            "section_id": "page-60",
            "page_number": 60,
            "title": "Consolidated Statements of Cash Flows",
            "content": {
                "rows": [
                    ["Consolidated Statements of Cash Flows"],
                    ["Years ended December 31", "2018", "2017"],
                    ["(Millions)"],
                    ["Purchases of property, plant and equipment", "(1,577)", "(1,373)"],
                ]
            },
            "source_anchor": "page-60-table-1",
        },
    ]

    evidence = rank_evidence(plan, sections=[], semantic_matches=[], tables=tables, facts=[])

    assert next(item for item in evidence if item.page_number == 60).score > next(
        item for item in evidence if item.page_number == 49
    ).score


def test_generic_period_table_uses_its_statement_section_for_preference_and_citation() -> None:
    question = "What was gross profit in FY2017 according to the income statement?"
    plan = plan_question(question)
    sections = [
        {"id": "highlights", "page_number": 25, "heading": "Five-Year Financial Highlights"},
        {
            "id": "earnings",
            "page_number": 54,
            "heading": "Consolidated Statements of Earnings",
        },
    ]
    tables = [
        {
            "id": "highlights-table",
            "section_id": "highlights",
            "page_number": 25,
            "title": "Five-Year Financial Highlights",
            "content": {"rows": [["Gross profit", "9,440"]]},
            "source_anchor": "page-25-table-1",
        },
        {
            "id": "earnings-table",
            "section_id": "earnings",
            "page_number": 54,
            "title": "Fiscal Years Ended",
            "content": {
                "rows": [
                    ["Fiscal Years Ended", "2017", "2016"],
                    ["Gross profit", "9,440", "9,191"],
                ]
            },
            "source_anchor": "page-54-table-1",
        },
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=tables, facts=[])
    formal_statement = next(item for item in evidence if item.page_number == 54)
    recap = next(item for item in evidence if item.page_number == 25)

    assert formal_statement.heading == "Consolidated Statements of Earnings"
    assert formal_statement.table_title == "Consolidated Statements of Earnings"
    assert formal_statement.score > recap.score
    result = generate_answer_result(question, evidence)
    assert result.status == "supported"
    assert "$9,440" in result.content
    assert result.citation_indices == (2,)


def test_split_contents_navigation_does_not_hide_substantive_evidence_or_leak_as_a_source_label() -> None:
    plan = plan_question("What was net cash provided by operating activities in 2022?")
    sections = [
        {
            "id": "real-toc",
            "page_number": 2,
            "heading": "T able of Contents",
            "content": (
                "T able of Contents Beginning Page PART I ITEM 1 Business 4 "
                "ITEM 1A Risk Factors 10 ITEM 2 Properties 16 ITEM 3 Legal Proceedings 17"
            ),
            "source_anchor": "page-2",
        },
        {
            "id": "cash-flow-page",
            "page_number": 39,
            "heading": "T able of Contents",
            "content": (
                "T able of Contents Cash Flows from Operating Activities. "
                "Net cash provided by operating activities was $5,591 million in 2022."
            ),
            "source_anchor": "page-39",
        },
    ]
    tables = [
        {
            "id": "toc-table",
            "section_id": "real-toc",
            "page_number": 2,
            "title": "T able of Contents",
            "content": {"rows": [["ITEM 1 Business", "4"], ["ITEM 2 Properties", "16"], ["ITEM 3 Legal Proceedings", "17"]]},
            "source_anchor": "page-2-table-1",
        },
        {
            "id": "cash-flow-table",
            "section_id": "cash-flow-page",
            "page_number": 39,
            "title": "T able of Contents",
            "content": {
                "rows": [
                    ["Year ended December 31, (Millions)", "2022", "2021"],
                    ["Net cash provided by operating activities", "$5,591", "$7,454"],
                ]
            },
            "source_anchor": "page-39-table-1",
        },
    ]

    evidence = rank_evidence(plan, sections=sections, semantic_matches=[], tables=tables, facts=[])

    assert all(item.page_number != 2 for item in evidence)
    assert any(item.page_number == 39 and item.source_type == "table" for item in evidence)
    assert all(item.heading != "T able of Contents" for item in evidence)
    assert all(item.table_title != "T able of Contents" for item in evidence if item.source_type == "table")
