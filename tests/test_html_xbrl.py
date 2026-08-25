from pathlib import Path

from backend.app.services.html_xbrl import _repair_display_label, parse_html_xbrl


def test_jpmorgan_inline_xbrl_extract_is_page_addressable() -> None:
    extract = parse_html_xbrl(Path("sample-data/JPMORGAN_2022Q2_10Q.htm"))
    assert len(extract.pages) == 177
    assert len(extract.tables) > 200
    assert len(extract.facts) > 5000
    assert all(page.number == index for index, page in enumerate(extract.pages, start=1))
    assert any(fact.concept == "dei:DocumentType" and fact.value == "10-Q" for fact in extract.facts)
    assert all(fact.page_number for fact in extract.facts)
    assert all(fact.source_anchor for fact in extract.facts)
    assert {chunk.content_type for chunk in extract.chunks} == {"narrative", "table"}


def test_jpmorgan_inline_xbrl_facts_keep_context_dates_and_numeric_semantics() -> None:
    extract = parse_html_xbrl(Path("sample-data/JPMORGAN_2022Q2_10Q.htm"))

    investment_banking_revenue = next(
        fact
        for fact in extract.facts
        if fact.concept == "us-gaap:InvestmentBankingRevenue"
        and fact.period_start == "2022-01-01"
        and fact.period_end == "2022-03-31"
        and fact.value == "2,008"
    )
    assert investment_banking_revenue.context_ref
    assert investment_banking_revenue.unit == "usd"
    assert investment_banking_revenue.scale == "6"
    assert investment_banking_revenue.normalized_value == "2008000000"
    assert investment_banking_revenue.page_number == 80

    loss = next(
        fact
        for fact in extract.facts
        if fact.concept == "us-gaap:DebtSecuritiesAvailableForSaleRealizedGainLoss"
        and fact.sign == "-"
        and fact.page_number == 80
    )
    assert loss.normalized_value == "-394000000"


def test_jpmorgan_statement_tables_use_the_visible_statement_heading() -> None:
    extract = parse_html_xbrl(Path("sample-data/JPMORGAN_2022Q2_10Q.htm"))
    income_statement_table = next(table for table in extract.tables if table.page_number == 80)
    cash_flow_table = next(table for table in extract.tables if table.page_number == 84)

    assert income_statement_table.title == "Consolidated statements of income (unaudited)"
    assert cash_flow_table.title == "Consolidated statements of cash flows (unaudited)"


def test_display_labels_repair_inline_xbrl_word_splits_without_editing_evidence_body() -> None:
    assert _repair_display_label("Consolidated Balance Shee t") == "Consolidated Balance Sheet"
    assert _repair_display_label("Consolidated Statement of Cash Flow s — (Millions)") == "Consolidated Statement of Cash Flows — (Millions)"
    assert _repair_display_label("RESULTS OF OPERATI ONS") == "RESULTS OF OPERATIONS"
