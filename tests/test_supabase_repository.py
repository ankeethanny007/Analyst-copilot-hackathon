from backend.app.services.supabase_repository import SupabaseRepository


def _repository_with_select(fake_select):
    repository = object.__new__(SupabaseRepository)
    repository.select = fake_select
    return repository


def test_owner_document_and_section_reads_are_bounded_and_scoped() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_select(table: str, params: dict[str, str]):
        calls.append((table, params))
        return []

    repository = _repository_with_select(fake_select)

    repository.documents_for_owner("owner-1", limit=9999)
    repository.document_for_owner("document-1", "owner-1")
    repository.all_sections("document-1", limit=9999)
    repository.xbrl_facts("document-1", limit=9999)

    documents, document, sections, facts = calls
    assert documents[0] == "documents"
    assert documents[1]["owner_id"] == "eq.owner-1"
    assert documents[1]["limit"] == "200"
    assert document[1]["id"] == "eq.document-1"
    assert document[1]["owner_id"] == "eq.owner-1"
    assert sections[1]["limit"] == "1000"
    assert "source_anchor" in sections[1]["select"]
    assert facts[1]["limit"] == "1000"
    assert "normalized_value" in facts[1]["select"]
    assert "scale" in facts[1]["select"]


def test_owner_scoped_message_evidence_reads_snapshots() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_select(table: str, params: dict[str, str]):
        calls.append((table, params))
        if table == "messages":
            return [{"id": "message-1", "chat_topic_id": "topic-1", "role": "assistant", "answer_status": "supported"}]
        if table == "message_evidence":
            return [{"ordinal": 1, "page_number": 64, "section_heading": "Income statement"}]
        return []

    repository = _repository_with_select(fake_select)
    repository.topic_for_owner = lambda topic_id, owner_id: {"id": topic_id} if owner_id == "owner-1" else None

    result = repository.message_evidence_for_owner("message-1", "owner-1")

    assert result == [{"ordinal": 1, "page_number": 64, "section_heading": "Income statement"}]
    evidence_call = calls[-1]
    assert evidence_call[0] == "message_evidence"
    assert "section_heading" in evidence_call[1]["select"]
    assert "table_title" in evidence_call[1]["select"]


def test_message_evidence_is_not_exposed_for_an_abstention() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_select(table: str, params: dict[str, str]):
        calls.append((table, params))
        if table == "messages":
            return [{"id": "message-1", "chat_topic_id": "topic-1", "role": "assistant", "answer_status": "not_found"}]
        raise AssertionError(f"Unexpected evidence lookup: {table}")

    repository = _repository_with_select(fake_select)
    repository.topic_for_owner = lambda topic_id, owner_id: {"id": topic_id} if owner_id == "owner-1" else None

    assert repository.message_evidence_for_owner("message-1", "owner-1") == []
    assert [table for table, _ in calls] == ["messages"]


def test_relevant_xbrl_facts_searches_concepts_before_using_a_small_fallback() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_select(table: str, params: dict[str, str]):
        calls.append((table, params))
        if "or" in params:
            return [{"id": "late-revenue", "concept": "Revenues", "page_number": 80}]
        return [{"id": "early-fallback", "concept": "DocumentType", "page_number": 1}]

    repository = _repository_with_select(fake_select)

    rows = repository.relevant_xbrl_facts("document-1", ("total", "revenue", "2022"))

    assert {row["id"] for row in rows} == {"late-revenue", "early-fallback"}
    search = calls[0][1]
    assert search["document_id"] == "eq.document-1"
    assert "concept.ilike.*revenue*" in search["or"]
    assert calls[1][1]["limit"] == "120"
