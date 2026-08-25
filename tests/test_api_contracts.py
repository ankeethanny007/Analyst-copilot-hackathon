from fastapi.testclient import TestClient
import pytest

pytest.importorskip("jwt")

from app.api import routes
from app.main import app

DOCUMENT_ID = "00000000-0000-0000-0000-000000000001"
TOPIC_ID = "00000000-0000-0000-0000-000000000002"


class FakeRepository:
    def __init__(self, status: str = "ready") -> None:
        self.status = status
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []

    def topic_for_owner(self, topic_id: str, owner_id: str):
        return {"id": topic_id, "document_id": DOCUMENT_ID, "title": "Topic"} if owner_id == "owner-1" else None

    def document_for_owner(self, document_id: str, owner_id: str):
        if document_id != DOCUMENT_ID or owner_id != "owner-1":
            return None
        return {"id": document_id, "original_filename": "JPM.htm", "status": self.status}

    def all_sections(self, document_id: str):
        assert document_id == DOCUMENT_ID
        return [{"id": "section-1", "page_number": 80, "ordinal": 1, "heading": "Consolidated statements of income", "content": "Three months ended March 31 (in millions) Total net revenue $ 30,717 $ 32,266", "source_anchor": "page-80"}]

    def tables(self, document_id: str):
        return [{"id": "table-1", "section_id": "section-1", "page_number": 80, "title": "Consolidated statements of income", "content": {"rows": [["Three months ended March 31 (in millions)", "2022", "2021"], ["Total net revenue", "$", "30,717", "$", "32,266"]]}, "source_anchor": "page-80-table-1"}]

    def xbrl_facts(self, document_id: str):
        return []

    def relevant_xbrl_facts(self, document_id: str, terms):
        return self.xbrl_facts(document_id)

    def match_chunks(self, document_id: str, embedding: list[float], limit: int):
        return []

    def insert(self, table: str, row: dict):
        self.inserts.append((table, row))
        if table == "messages":
            return {"id": f"message-{len([name for name, _ in self.inserts if name == 'messages'])}", **row, "created_at": "2026-08-25T00:00:00Z"}
        return {"id": f"{table}-1", **row}

    def update(self, table: str, where: dict, row: dict):
        self.updates.append((table, where, row))
        return row

    def documents_for_owner(self, owner_id: str):
        return [{"id": DOCUMENT_ID, "original_filename": "JPM.htm", "status": self.status}]

    def document_page_for_owner(self, document_id: str, page_number: int, owner_id: str):
        if document_id == DOCUMENT_ID and page_number == 80 and owner_id == "owner-1":
            return {"document_id": document_id, "page_number": page_number, "source_anchor": "page-80", "content": "page content"}
        return None

    def select(self, table: str, params: dict):
        if table == "processing_jobs":
            return [{"status": self.status, "stage": "complete", "progress": 100}]
        return []

    def message_evidence_for_owner(self, message_id: str, owner_id: str):
        return [] if owner_id == "owner-1" else None


def _client(repository: FakeRepository, monkeypatch):
    app.dependency_overrides[routes.current_owner_id] = lambda: "owner-1"
    app.dependency_overrides[routes.repository] = lambda: repository
    monkeypatch.setattr(routes, "embed_question", lambda question: [0.1, 0.2])
    return TestClient(app)


def test_chat_rejects_a_question_until_its_filing_is_ready(monkeypatch) -> None:
    repository = FakeRepository(status="processing")
    client = _client(repository, monkeypatch)
    try:
        response = client.post(f"/v1/chat-topics/{TOPIC_ID}/messages", json={"content": "What was revenue?"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "still processing" in response.json()["detail"]
    assert not repository.inserts


def test_chat_persists_immutable_source_snapshot_for_a_supported_answer(monkeypatch) -> None:
    repository = FakeRepository()
    client = _client(repository, monkeypatch)
    try:
        response = client.post(f"/v1/chat-topics/{TOPIC_ID}/messages", json={"content": "What was total net revenue for the three months ended March 31, 2022?"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["assistant_message"]["answer_status"] == "supported"
    assert "$30,717 million" in body["assistant_message"]["content"]
    assert body["evidence"][0]["page_number"] == 80
    evidence_insert = next(row for table, row in repository.inserts if table == "message_evidence")
    assert evidence_insert["page_number"] == 80
    assert evidence_insert["section_heading"] == "Consolidated statements of income"
    assert evidence_insert["source_type"] == "table"


def test_document_library_and_page_are_owner_scoped(monkeypatch) -> None:
    repository = FakeRepository()
    client = _client(repository, monkeypatch)
    try:
        library = client.get("/v1/documents")
        page = client.get(f"/v1/documents/{DOCUMENT_ID}/pages/80")
        missing = client.get(f"/v1/documents/{DOCUMENT_ID}/pages/81")
    finally:
        app.dependency_overrides.clear()

    assert library.status_code == 200
    assert library.json()[0]["id"] == DOCUMENT_ID
    assert page.status_code == 200
    assert page.json()["source_anchor"] == "page-80"
    assert missing.status_code == 404


def test_persisted_sources_renumber_answer_citations_in_popup_order() -> None:
    sources = ["page-10", "page-64", "page-80"]

    content, cited = routes._cited_sources_for_persistence(
        "The answer is supported by [S3] and [S2].",
        sources,
        (3, 2),
    )

    assert content == "The answer is supported by [S2] and [S1]."
    assert cited == ["page-64", "page-80"]
