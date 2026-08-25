from backend.app.services.answering import RetrievedEvidence, generate_answer


def test_exact_metric_evidence_bypasses_unnecessary_model_abstention() -> None:
    evidence = [RetrievedEvidence(None, "section", 3, "Consolidated financial highlights", "(in millions) 1Q22 Total net revenue $ 30,717 $ 29,257", 1.0)]
    answer, status = generate_answer("What was total net revenue for the three months ended March 31, 2022?", evidence)
    assert status == "supported"
    assert answer == "Answer: Total net revenue was $30,717 million. [S1]"


def test_growth_calculation_uses_a_single_cited_annual_table() -> None:
    evidence = [RetrievedEvidence(None, "section", 14, "Consolidated Statement of Income", "(Dollars in millions) 2018 | 2017 Net sales | $ | 32,765 | $ | 31,657", 1.0)]
    answer, status = generate_answer("What was the growth in gross revenue from 2017 to 2018? Show the calculation.", evidence)
    assert status == "supported"
    assert "3.5%" in answer
    assert "$31,657 million" in answer
    assert "in 2017 to $32,765 million in 2018" in answer
    assert answer.endswith("[S1]")
