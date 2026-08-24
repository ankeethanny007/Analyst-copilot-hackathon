# Analyst Copilot

Evidence-first Q&A over SEC filings. A filing is uploaded, stored once in Cloudflare R2, processed once, and reused by one or more document-scoped chat topics.

## MVP architecture

```text
Next.js UI → FastAPI → Supabase PostgreSQL + pgvector
                    ↘ Cloudflare R2 (original files)
                    ↘ OpenAI (embeddings and answer generation)
```

The backend always derives the active document from the chat topic and scopes every retrieval query to that `document_id`. An answer is returned only with validated evidence; otherwise the API abstains with `not_found`.

## Local setup

1. Copy `.env.example` to `.env.local` and add Supabase, R2, and OpenAI credentials.
2. Run `database/migrations/0001_initial.sql` in the Supabase SQL editor.
3. Start the API: `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn app.main:app --reload`.
4. Start the UI: `cd frontend && npm install && npm run dev`.

The supplied JPMorgan Inline XBRL filing and the 136-question `practice-questions.jsonl` benchmark are in `sample-data/`. The filing is intentionally the first ingestion target; PDFs are supported as a fallback path after HTML/Inline XBRL. The benchmark is development/evaluation data, not an answer source at runtime.

## Delivery plan

- Day 1: storage, database, topic creation, upload/status UI.
- Day 2: HTML/Inline XBRL extraction, pages/sections/tables/facts/chunks, embeddings.
- Day 3: document-scoped retrieval, evidence validation, answer/abstention.
- Day 4: polished chat, source evidence drawer, animations and responsive states.
- Day 5: benchmark, context-isolation tests, demo hardening and approach note.

See `docs/implementation-plan.md`, `docs/api-contracts.md`, and `docs/approach-note.md`.
