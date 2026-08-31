import backend.app.services.answering as answering
from backend.app.services.answering import RetrievedEvidence, _direct_growth_answer, _response_text, generate_answer
from urllib.error import HTTPError


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


def test_growth_calculation_maps_requested_years_in_a_five_year_table() -> None:
    evidence = [
        RetrievedEvidence(
            None,
            "section",
            18,
            "Selected Consolidated Financial Data",
            "Year Ended December 31,\n2013\n2014\n2015\n2016\n2017\n(in millions)\n"
            "Net sales\n$74,452\n$88,988\n$107,006\n$135,987\n$177,866\n"
            "Operating income\n$745\n$178\n$2,233\n$4,186\n$4,106",
            1.0,
        )
    ]

    answer, status = generate_answer(
        "What is Amazon's year-over-year change in revenue from FY2016 to FY2017?",
        evidence,
    )

    assert status == "supported"
    assert "30.8%" in answer
    assert "from $135,987 million in 2016 to $177,866 million in 2017" in answer


def test_formula_input_change_does_not_trigger_unrelated_growth_shortcut() -> None:
    evidence = [
        RetrievedEvidence(
            None,
            "section",
            18,
            "Selected Consolidated Financial Data",
            "2016 2017 Net sales $135,987 $177,866",
            1.0,
        )
    ]

    answer = _direct_growth_answer(
        "What is FY2017 DPO? DPO is 365 * average accounts payable / "
        "(FY2017 COGS + change in inventory between FY2016 and FY2017).",
        evidence,
    )

    assert answer is None


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


def test_direct_metric_maps_a_requested_year_from_vertical_table_headers() -> None:
    evidence = [
        RetrievedEvidence(
            None,
            "table",
            38,
            "Consolidated Statements of Operations",
            "Year Ended December 31,\n2017\n2018\n2019\n"
            "Net product sales\n$118,573\n$141,915\n$160,408\n"
            "Operating income\n$4,106\n$12,421\n$14,541\n"
            "Net income\n$3,033\n$10,073\n$11,588",
            1.0,
        )
    ]

    answer, status = generate_answer(
        "What is Amazon's FY2019 net income attributable to shareholders in USD millions?",
        evidence,
    )

    assert status == "supported"
    assert "Net income was $11,588 million" in answer


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


def test_answer_generation_retries_a_transient_provider_failure(monkeypatch) -> None:
    evidence = [
        RetrievedEvidence(
            None,
            "section",
            42,
            "Management discussion and analysis",
            "Operating income declined because higher raw-material costs reduced margin.",
            1.0,
        )
    ]
    attempts = 0

    def post(_path, _body):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary provider failure")
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Higher raw-material costs reduced margin. [S1]"}]}]}

    monkeypatch.setattr(answering, "_openai_post", post)
    monkeypatch.setattr(answering.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("ANSWER_MAX_RETRIES", "1")

    answer, status = generate_answer("What drove the decline in operating income?", evidence)

    assert status == "supported"
    assert attempts == 2
    assert answer.endswith("[S1]")


def test_answer_generation_does_not_retry_permanent_provider_errors(monkeypatch) -> None:
    evidence = [
        RetrievedEvidence(
            None,
            "section",
            42,
            "Management discussion and analysis",
            "Operating income declined because higher raw-material costs reduced margin.",
            1.0,
        )
    ]
    attempts = 0

    def post(_path, _body):
        nonlocal attempts
        attempts += 1
        raise HTTPError("https://api.openai.com/v1/responses", 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr(answering, "_openai_post", post)
    monkeypatch.setenv("ANSWER_MAX_RETRIES", "2")

    answer, status = generate_answer("What drove the decline in operating income?", evidence)

    assert status == "failed"
    assert attempts == 1
    assert "could not be completed" in answer
