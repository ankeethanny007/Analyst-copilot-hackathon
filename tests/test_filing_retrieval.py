from backend.app.services.filing_retrieval import relevant_tables, table_heading


def test_table_retrieval_maps_analyst_revenue_language_to_sales_table() -> None:
    tables = [{"section_id": "income", "page_number": 12, "title": "Consolidated Statements of Income", "content": {"rows": [["Consolidated Statements of Income"], ["Net sales", "32,765", "31,657"], ["Net income", "5,349", "4,858"]]}}]
    selected = relevant_tables("What was the growth in gross revenue?", tables)
    assert selected and selected[0][0]["section_id"] == "income"
    assert table_heading(*selected[0][:2]) == "Consolidated Statements of Income"


def test_table_retrieval_maps_stakeholder_question_to_ownership_table() -> None:
    tables = [{"section_id": "owners", "page_number": 4, "title": "Beneficial ownership", "content": {"rows": [["Beneficial ownership"], ["Principal shareholders", "Shares"]]}}]
    assert relevant_tables("Who is the major stakeholder?", tables)[0][0]["section_id"] == "owners"


def test_annual_sales_row_beats_a_percentage_only_sales_table() -> None:
    tables = [
        {"section_id": "percentage", "page_number": 20, "title": "", "content": {"rows": [["2018", "2017", "Percent of net sales", "22.4%", "24.3%"]]}},
        {"section_id": "annual", "page_number": 56, "title": "", "content": {"rows": [["2018", "2017"], ["Net sales", "$", "32,765", "$", "31,657"]]}},
    ]
    assert relevant_tables("What was revenue growth from 2017 to 2018?", tables)[0][0]["section_id"] == "annual"
