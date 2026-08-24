from pathlib import Path

from backend.app.services.html_xbrl import parse_html_xbrl


def test_jpmorgan_inline_xbrl_extract_is_page_addressable() -> None:
    extract = parse_html_xbrl(Path("sample-data/JPMORGAN_2022Q2_10Q.htm"))
    assert len(extract.pages) == 177
    assert len(extract.tables) > 200
    assert len(extract.facts) > 5000
    assert all(page.number == index for index, page in enumerate(extract.pages, start=1))
    assert any(fact.concept == "dei:DocumentType" and fact.value == "10-Q" for fact in extract.facts)
    assert any(fact.page_number for fact in extract.facts)
    assert {chunk.content_type for chunk in extract.chunks} == {"narrative", "table"}
