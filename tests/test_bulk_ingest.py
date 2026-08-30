from pathlib import Path

from backend.scripts.bulk_ingest import expected_document_names, normalise_filename, resolve_files


def test_expected_document_names_preserves_first_benchmark_occurrence() -> None:
    cases = [
        {"doc_name": "Alpha_2024_10K.htm"},
        {"doc_name": "Beta_2024_10K.htm"},
        {"doc_name": "Alpha_2024_10K.htm"},
    ]

    assert expected_document_names(cases) == ["Alpha_2024_10K.htm", "Beta_2024_10K.htm"]


def test_resolve_files_matches_normalized_html_stems(tmp_path: Path) -> None:
    filing = tmp_path / "Alpha-2024_10K.htm"
    filing.write_text("<html></html>", encoding="utf-8")

    resolved, errors = resolve_files(tmp_path, ["Alpha_2024_10K.htm"])

    assert errors == []
    assert resolved["Alpha_2024_10K.htm"] == filing
    assert normalise_filename("Alpha_2024_10K.htm") == "alpha202410k"


def test_resolve_files_reports_missing_or_ambiguous_filing(tmp_path: Path) -> None:
    (tmp_path / "Alpha-2024_10K.htm").write_text("one", encoding="utf-8")
    (tmp_path / "Alpha_2024_10K.html").write_text("two", encoding="utf-8")

    resolved, errors = resolve_files(tmp_path, ["Alpha_2024_10K.htm", "Missing_2024_10K.htm"])

    assert resolved == {}
    assert any("Ambiguous" in error for error in errors)
    assert any("Missing" in error for error in errors)
