import backend.app.services.answering as answering
from backend.app.services.answering import RetrievedEvidence, _response_text, generate_answer


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


def test_direct_metric_converts_a_source_million_value_to_requested_billions() -> None:
    evidence = [RetrievedEvidence(None, "section", 57, "Consolidated Balance Sheet", "(Dollars in millions) 2018 | Property, plant and equipment — net | $ | 8,700", 1.0)]

    answer, status = generate_answer("What is FY2018 net PPNE in USD billions?", evidence)

    assert status == "supported"
    assert "$8.70 billion" in answer
    assert answer.endswith("[S1]")


def test_net_ppe_does_not_confuse_the_gross_balance_sheet_line_for_the_net_line() -> None:
    evidence = [
        RetrievedEvidence(
            None,
            "section",
            58,
            "Consolidated Balance Sheet",
            "(Dollars in millions) 2018 | Property, plant and equipment | 24,873 | "
            "Property, plant and equipment — net | 8,738",
            1.0,
        )
    ]

    answer, status = generate_answer("What was net PP&E in fiscal year 2018 in USD billions?", evidence)

    assert status == "supported"
    assert "$8.74 billion" in answer
    assert "$24.87 billion" not in answer


def test_response_text_reads_raw_responses_api_message_content() -> None:
    response = {
        "output": [
            {"type": "reasoning", "content": []},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Higher input costs drove the decline. [S1]"},
                ],
            },
        ]
    }

    assert _response_text(response) == "Higher input costs drove the decline. [S1]"


def test_driver_question_bypasses_metric_shortcut_and_uses_cited_evidence(monkeypatch) -> None:
    evidence = [
        RetrievedEvidence(
            None,
            "section",
            42,
            "Management discussion and analysis",
            "Operating income declined because higher raw-material costs and lower selling prices reduced margin. Operating income margin was 22.4%.",
            1.0,
        )
    ]
    monkeypatch.setattr(
        answering,
        "_openai_post",
        lambda path, body: {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Higher raw-material costs and lower selling prices reduced margin. [S1]"}]}]},
    )

    answer, status = generate_answer("What led to the decline in operating income in 2018?", evidence)

    assert status == "supported"
    assert "Higher raw-material costs" in answer
    assert "22.4" not in answer
