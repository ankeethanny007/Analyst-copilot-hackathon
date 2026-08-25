from backend.app.services.benchmark_evaluation import score_case, summarize


CASE = {
    "financebench_id": "case-1",
    "doc_name": "Example_2022_10K",
    "question": "What was revenue?",
    "answer": "$1,577.00",
    "question_type": "metrics-generated",
    "evidence": [{"evidence_page_num": 59}],
}


def test_benchmark_awards_a_point_only_for_answer_and_location() -> None:
    correct = score_case(CASE, status="supported", actual_answer="Answer: $1,577 million. [S1]", cited_pages=[59])
    wrong_location = score_case(CASE, status="supported", actual_answer="Answer: $1,577 million. [S1]", cited_pages=[58])
    wrong_answer = score_case(CASE, status="supported", actual_answer="Answer: $1,200 million. [S1]", cited_pages=[59])
    abstained = score_case(CASE, status="not_found", actual_answer="Not found in this filing.", cited_pages=[])

    assert correct.score == 1
    assert wrong_location.score == 0
    assert wrong_answer.score == -1
    assert abstained.score == 0
    report = summarize([correct, wrong_location, wrong_answer, abstained])
    assert report["score"] == 0
    assert report["correct_with_location"] == 1
    assert report["correct_wrong_location"] == 1
