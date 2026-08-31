from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock

pytest.importorskip("jwt")

from app.api import routes
from app.main import app

DOCUMENT_ID = "00000000-0000-0000-0000-000000000001"
TOPIC_ID = "00000000-0000-0000-0000-000000000002"


class FakeRepository:
    def __init__(self, status: str = "ready", messages: list[dict] | None = None, direct_evidence: list[dict] | None = None) -> None:
        self.status = status
        self.messages = messages or []
        self.direct_evidence = direct_evidence or []
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []
        self.deletes: list[tuple[str, dict]] = []

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

    def delete(self, table: str, where: dict):
        self.deletes.append((table, where))

    def documents_for_owner(self, owner_id: str):
        return [{"id": DOCUMENT_ID, "original_filename": "JPM.htm", "status": self.status}]

    def document_page_for_owner(self, document_id: str, page_number: int, owner_id: str):
        if document_id == DOCUMENT_ID and page_number == 80 and owner_id == "owner-1":
            return {"document_id": document_id, "page_number": page_number, "source_anchor": "page-80", "content": "page content"}
        return None

    def select(self, table: str, params: dict):
        if table == "processing_jobs":
            return [{"status": self.status, "stage": "complete", "progress": 100}]
        if table == "messages":
            return self.messages
        return []

    def message_evidence_for_owner(self, message_id: str, owner_id: str):
        return self.direct_evidence if owner_id == "owner-1" else None


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


def test_upload_rejects_a_filename_content_identity_mismatch_before_persistence(monkeypatch) -> None:
    repository = FakeRepository()
    client = _client(repository, monkeypatch)
    message = (
        "Incorrect file. Based on the filename \u201c3M_2023Q2_10Q.htm\u201d, expected FY2023 Q2 Form 10-Q, "
        "but the file contains FY2023 Q1 Form 10-Q (period ended March 31, 2023) instead."
    )
    monkeypatch.setattr(routes, "sha256_upload", AsyncMock(return_value=("checksum", 123)))
    monkeypatch.setattr(routes, "validate_upload_identity", AsyncMock(return_value=message))
    try:
        response = client.post(
            "/v1/documents",
            files={"file": ("3M_2023Q2_10Q.htm", b"<html></html>", "text/html")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == message
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


def test_exact_direct_metric_uses_fast_lexical_retrieval_without_an_embedding_call(monkeypatch) -> None:
    repository = FakeRepository()
    client = _client(repository, monkeypatch)
    monkeypatch.setattr(routes, "embed_question", lambda _question: (_ for _ in ()).throw(AssertionError("embedding should not run")))
    try:
        response = client.post(
            f"/v1/chat-topics/{TOPIC_ID}/messages",
            json={"content": "What was total net revenue for the three months ended March 31, 2022?"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["assistant_message"]["answer_status"] == "supported"


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


def test_deleting_a_chat_leaves_the_persistent_filing_untouched(monkeypatch) -> None:
    repository = FakeRepository()
    client = _client(repository, monkeypatch)
    try:
        response = client.delete(f"/v1/chat-topics/{TOPIC_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert repository.deletes == [
        ("chat_topics", {"id": f"eq.{TOPIC_ID}", "owner_id": "eq.owner-1"})
    ]
    assert repository.documents_for_owner("owner-1")[0]["id"] == DOCUMENT_ID


def test_persisted_sources_renumber_answer_citations_in_popup_order() -> None:
    sources = ["page-10", "page-64", "page-80"]

    content, cited = routes._cited_sources_for_persistence(
        "The answer is supported by [S3] and [S2].",
        sources,
        (3, 2),
    )

    assert content == "The answer is supported by [S2] and [S1]."
    assert cited == ["page-64", "page-80"]


def test_list_messages_hides_legacy_evidence_when_the_answer_abstains(monkeypatch) -> None:
    legacy_evidence = [{"ordinal": 1, "page_number": 1, "section_heading": "Table of Contents", "excerpt": "Unrelated source"}]
    repository = FakeRepository(
        messages=[
            {"id": "user-1", "role": "user", "content": "Who is the major shareholder?", "answer_status": None, "created_at": "2026-08-25T00:00:00Z", "message_evidence": legacy_evidence},
            {"id": "assistant-1", "role": "assistant", "content": "Not found in this filing.", "answer_status": "not_found", "created_at": "2026-08-25T00:00:01Z", "message_evidence": legacy_evidence},
            {"id": "assistant-2", "role": "assistant", "content": "Revenue was $30,717 million. [S1]", "answer_status": "supported", "created_at": "2026-08-25T00:00:02Z", "message_evidence": legacy_evidence},
        ]
    )
    client = _client(repository, monkeypatch)
    try:
        response = client.get(f"/v1/chat-topics/{TOPIC_ID}/messages")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    messages = response.json()
    assert messages[0]["message_evidence"] == []
    assert messages[1]["message_evidence"] == []
    assert messages[2]["message_evidence"] == legacy_evidence


def test_message_evidence_endpoint_returns_empty_for_an_abstention(monkeypatch) -> None:
    repository = FakeRepository(direct_evidence=[])
    client = _client(repository, monkeypatch)
    try:
        response = client.get("/v1/messages/00000000-0000-0000-0000-000000000003/evidence")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []
