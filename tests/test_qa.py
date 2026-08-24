from pathlib import Path

from backend.app.services.html_xbrl import parse_html_xbrl
from backend.app.services.qa import answer


def test_retrieval_returns_exact_page_evidence() -> None:
    extract = parse_html_xbrl(Path("sample-data/JPMORGAN_2022Q2_10Q.htm"))
    result = answer("What is the date of the report?", extract)
    assert result.status == "evidence_ready"
    assert result.evidence and result.source_summary and result.source_summary.startswith("Page ")


def test_retrieval_abstains_when_no_evidence_exists() -> None:
    extract = parse_html_xbrl(Path("sample-data/JPMORGAN_2022Q2_10Q.htm"))
    result = answer("What is the capital city of Mars?", extract)
    assert result.status == "not_found"
    assert result.content == "Not found in this filing."
