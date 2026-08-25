# Analyst Copilot

Evidence-first Q&A over SEC filings. A filing is uploaded, stored once in Cloudflare R2, processed once, and reused by one or more document-scoped chat topics.

## MVP architecture

```text
Next.js UI → FastAPI → Supabase PostgreSQL + pgvector
                    ↘ Cloudflare R2 (original files)
                    ↘ OpenAI (embeddings and answer generation)
```

The backend always derives the active document from the chat topic and scopes every retrieval query to that `document_id`. It combines lexical sections, semantic chunks, statement tables, and page-addressable Inline XBRL facts before generating a cited answer. An answer is returned only with validated source labels; otherwise the API abstains with `Not found in this filing.`

## Local setup

1. Copy `.env.example` to `.env.local` and add Supabase, R2, and OpenAI credentials.
2. For a new project, run the SQL files in `database/migrations/` in numeric order. For an existing project that already has `0001`, apply `0002_evidence_snapshots_and_xbrl_normalization.sql` and `0003_topic_document_owner_integrity.sql` once.
3. Start the API: `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn app.main:app --reload`.
4. Start the UI: `cd frontend && npm install && npm run dev`.

After applying `0002`, use **Retry processing** on existing filings once. That refreshes their table headings, Inline XBRL periods and normalized values while preserving the snapshot evidence shown in historical chats.

The supplied JPMorgan Inline XBRL filing and the 136-question `practice-questions.jsonl` benchmark are in `sample-data/`. The filing is intentionally the first ingestion target; PDFs have a page-text fallback after HTML/Inline XBRL (OCR, table extraction, and XBRL are intentionally not attempted for PDF). The benchmark is development/evaluation data, not an answer source at runtime. Once matching filings are uploaded and ready, run it with:

```bash
PYTHONPATH=. python backend/scripts/evaluate_benchmark.py \
  --api-base http://127.0.0.1:8000 \
  --output /tmp/analyst-copilot-benchmark.json
```

Some FinanceBench records store a zero-based PDF page index while this HTML
parser displays its first source page as Page 1. In that case add
`--page-offset 1` for comparison only; the product still displays the actual
filing page it retrieved.

Run the regression suite with `PYTHONPATH=.:backend backend/.venv/bin/pytest -q tests`.

## Delivery plan

- Day 1: storage, database, topic creation, upload/status UI.
- Day 2: HTML/Inline XBRL extraction, pages/sections/tables/facts/chunks, embeddings.
- Day 3: document-scoped retrieval, evidence validation, answer/abstention.
- Day 4: polished chat, source evidence drawer, animations and responsive states.
- Day 5: benchmark, context-isolation tests, demo hardening and approach note.

See `docs/implementation-plan.md`, `docs/api-contracts.md`, and `docs/approach-note.md`.
