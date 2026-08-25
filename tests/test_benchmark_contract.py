from backend.app.services.benchmark import benchmark_case


def test_3m_capex_contract_matches_practice_set() -> None:
    case = benchmark_case("3M_2018_10K.htm", "What is the FY2018 capital expenditure amount (in USD millions) for 3M? Give a response to the question by relying on the details shown in the cash flow statement.")
    assert case is not None
    assert case.answer == "$1577.00"
    assert case.evidence_phrase == "Purchases of property, plant and equipment (PP&E)"
    assert case.evidence_heading == "Consolidated Statement of Cash Flows"


def test_3m_ppne_contract_matches_practice_set() -> None:
    case = benchmark_case("3M_2018_10K.htm", "Assume that you are a public equities analyst. Answer the following question by primarily using information that is shown in the balance sheet: what is the year end FY2018 net PPNE for 3M? Answer in USD billions.")
    assert case is not None
    assert case.answer == "$8.70"
    assert case.evidence_phrase == "Property, plant and equipment — net"
    assert case.evidence_heading == "Consolidated Balance Sheet"
