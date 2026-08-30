from datetime import datetime, timezone

from backend.scripts.report_benchmark_messages import format_check, latest_answer_for_question


def test_latest_answer_uses_the_reply_before_the_next_user_turn() -> None:
    messages = [
        {"id": "u1", "role": "user", "content": "Question?", "created_at": "2026-08-30T00:00:00+00:00"},
        {"id": "a1", "role": "assistant", "content": "First [S1]", "answer_status": "supported"},
        {"id": "u2", "role": "user", "content": "Something else", "created_at": "2026-08-30T00:01:00+00:00"},
        {"id": "u3", "role": "user", "content": "Question?", "created_at": "2026-08-30T00:02:00+00:00"},
        {"id": "a3", "role": "assistant", "content": "Latest [S1]", "answer_status": "supported"},
    ]

    user, assistant = latest_answer_for_question(messages, "Question?", datetime(2026, 8, 30, tzinfo=timezone.utc)) or (None, None)

    assert user and user["id"] == "u3"
    assert assistant and assistant["id"] == "a3"


def test_format_check_requires_sources_only_for_supported_answers() -> None:
    assert format_check({"answer_status": "supported", "content": "Answer [S1]", "message_evidence": [{"page_number": 3}]}) == {
        "supported_has_citation": True,
        "supported_has_sources": True,
        "non_supported_has_no_sources": True,
    }
    assert format_check({"answer_status": "not_found", "content": "Not found in this filing.", "message_evidence": []}) == {
        "supported_has_citation": True,
        "supported_has_sources": True,
        "non_supported_has_no_sources": True,
    }
