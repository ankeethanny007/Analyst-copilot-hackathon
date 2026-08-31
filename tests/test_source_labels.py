from pathlib import Path

from backend.app.services.html_xbrl import parse_html_xbrl
from backend.app.services.source_labels import (
    display_source_heading,
    is_real_table_of_contents,
    is_real_table_of_contents_table,
    is_table_of_contents_label,
)


def test_split_contents_navigation_is_not_a_real_contents_page() -> None:
    substantive = "T able of Contents Cash Flows from Operating Activities Net cash provided by operating activities was $5,591."
    real_contents = (
        "T able of Contents Beginning Page PART I ITEM 1 Business 4 "
        "ITEM 1A Risk Factors 10 ITEM 2 Properties 16 ITEM 3 Legal Proceedings 17"
    )

    assert is_table_of_contents_label("T able of Contents")
    assert not is_real_table_of_contents(substantive)
    assert is_real_table_of_contents(real_contents)
    assert is_real_table_of_contents_table("T able of Contents", "ITEM 1 Business 4 ITEM 2 Properties 16 ITEM 3 Legal Proceedings 17")
    assert is_real_table_of_contents_table("Page 2", "ITEM 1 Business 4 ITEM 2 Properties 16 ITEM 3 Legal Proceedings 17")
    assert not is_real_table_of_contents_table(
        "T able of Contents",
        "Year ended December 31, (Millions) | 2022 | 2021\nNet cash provided by operating activities | $5,591 | $7,454",
    )


def test_contents_page_is_detected_after_cover_preamble() -> None:
    contents_after_cover = (
        "ACME CORPORATION 2024 ANNUAL REPORT. This report covers the year ended December 31, 2024. "
        "T able of Contents Beginning Page PART I ITEM 1 Business 4 "
        "ITEM 1A Risk Factors 10 ITEM 2 Properties 16 ITEM 3 Legal Proceedings 17"
    )

    assert is_real_table_of_contents(contents_after_cover)
    assert is_real_table_of_contents_table(
        "Page 2",
        "ACME CORPORATION 2024 ANNUAL REPORT. Table of Contents "
        "ITEM 1 Business 4 ITEM 1A Risk Factors 10 ITEM 2 Properties 16",
    )
    assert is_real_table_of_contents(
        "Cover matter. TableofContents ITEM 1 Business 4 ITEM 1A Risk Factors 10 ITEM 2 Properties 16"
    )


def test_ordinary_see_note_references_are_not_contents_structure() -> None:
    financial_page = (
        "T able of Contents Consolidated Financial Statements. See Note 13 for details of the tax provision. "
        "See Note 14 for information about pension obligations. See Note 15 for derivative disclosures. "
        "Net income was $1,234 million for 2024."
    )
    financial_table = (
        "Year ended December 31, 2024 | 2023\n"
        "See Note 13 for income taxes | $120 | $110\n"
        "See Note 14 for pensions | $80 | $75\n"
        "See Note 15 for derivatives | $10 | $12"
    )

    assert not is_real_table_of_contents(financial_page)
    assert not is_real_table_of_contents_table("Page 52", financial_table)


def test_split_contents_navigation_gets_a_substantive_or_neutral_display_heading(tmp_path: Path) -> None:
    filing = tmp_path / "split-navigation.htm"
    filing.write_text(
        """
        <html><body>
          <div><a>T</a><a>able of </a><a>Contents</a></div>
          <div><span>Cash Flows from Operating Activities:</span></div>
          <table>
            <tr><td>Year ended December 31, (Millions)</td><td>2022</td><td>2021</td></tr>
            <tr><td>Net cash provided by operating activities</td><td>$5,591</td><td>$7,454</td></tr>
          </table>
        </body></html>
        """,
        encoding="utf-8",
    )

    extract = parse_html_xbrl(filing)

    assert extract.sections[0].heading == "Cash Flows from Operating Activities"
    assert extract.tables[0].title == "Cash Flows from Operating Activities"
    assert display_source_heading("T able of Contents") == "Filing section"
