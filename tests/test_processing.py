from backend.app.services.html_xbrl import Chunk, FilingExtract, Page, Section
from backend.app.services import processing
from backend.app.services.processing import _persist_extract


class FakeRepository:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.cleared: list[str] = []

    def clear_processed_content(self, document_id: str) -> None:
        self.cleared.append(document_id)

    def insert_many(self, table: str, rows: list[dict]) -> None:
        self.rows[table] = rows


def test_reprocessing_assigns_unique_stable_ids_to_repeated_chunks() -> None:
    extract = FilingExtract(
        pages=[Page(1, "page-1", "Repeated text")],
        sections=[Section(1, 1, "Filing section", "Repeated text", "page-1")],
        tables=[],
        facts=[],
        chunks=[
            Chunk(1, 1, "Repeated text", "narrative"),
            Chunk(1, 1, "Repeated text", "narrative"),
        ],
    )
    first = FakeRepository()
    second = FakeRepository()

    _persist_extract(first, "document-1", extract)
    _persist_extract(second, "document-1", extract)

    first_ids = [row["id"] for row in first.rows["document_chunks"]]
    second_ids = [row["id"] for row in second.rows["document_chunks"]]
    assert len(first_ids) == len(set(first_ids)) == 2
    assert first_ids == second_ids


def test_embedding_batch_size_is_bounded_and_configurable(monkeypatch) -> None:
    monkeypatch.delenv("EMBEDDING_BATCH_SIZE", raising=False)
    assert processing._embedding_batch_size() == 50

    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "250")
    assert processing._embedding_batch_size() == 100

    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "not-a-number")
    assert processing._embedding_batch_size() == 50
