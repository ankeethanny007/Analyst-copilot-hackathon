from backend.app.services.answering import RetrievedEvidence, generate_answer


def test_exact_metric_evidence_bypasses_unnecessary_model_abstention() -> None:
    evidence = [RetrievedEvidence(None, "section", 3, "Consolidated financial highlights", "(in millions) 1Q22 Total net revenue $ 30,717 $ 29,257", 1.0)]
    answer, status = generate_answer("What was total net revenue for the three months ended March 31, 2022?", evidence)
    assert status == "supported"
    assert answer == "Answer: Total net revenue was $30,717 million. [S1]"
