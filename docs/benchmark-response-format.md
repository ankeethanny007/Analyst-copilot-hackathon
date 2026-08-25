# Benchmark-driven response and evidence format

The bundled `sample-data/practice-questions.jsonl` is the FinanceBench-style
acceptance dataset for this MVP: 136 questions spanning information extraction,
metrics, numerical reasoning, and domain-relevant analysis. Every reference
item identifies one or more page-level source excerpts.

## Product format

The model receives only evidence retrieved from the active filing and uses the
following response forms:

```text
Answer: <exact requested fact, value, unit, or date>. [S1]
```

```text
Conclusion: <supported finding>. [S1]
Calculation: <source value> / <source value> = <result>. [S1] [S2]
```

The compact chat link renders the same evidence in page order:

```text
Page 59 · Consolidated Statement of Cash Flows +1 more
```

Opening it shows the exact stored excerpt for each numbered source. The server
must abstain when a value, unit, period, or calculation is not supported by
those excerpts.

## Benchmark mapping

| JSONL field | Application use |
| --- | --- |
| `question` | Test prompt / response format selector |
| `answer` | Expected normalized answer for evaluation |
| `question_type`, `question_reasoning` | Extraction vs. reasoning evaluation slice |
| `evidence[].evidence_page_num` | Expected page-level retrieval check |
| `evidence[].evidence_text` | Reference excerpt comparison; never injected into another filing's chat |

The JSONL is an evaluation and formatting source, not a substitute for
document-scoped retrieval and not a fine-tuning dataset for this MVP.
