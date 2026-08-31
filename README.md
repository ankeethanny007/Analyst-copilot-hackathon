# Analyst Copilot

Analyst Copilot is an evidence-first research assistant for SEC filings. Upload a filing once, then ask questions in document-scoped chats with page- and section-level sources.

## 1. Run locally after cloning and adding `.env`

### Prerequisites

- Node.js 20+
- Python 3.11+
- A Supabase project with `pgvector`
- A Cloudflare R2 bucket for original filing files
- An OpenAI API key for embeddings and answer generation

### Setup

```bash
git clone https://github.com/ankeethanny007/Analyst-copilot-hackathon.git
cd Analyst-copilot-hackathon
cp .env.example .env.local
```

Fill in `.env.local` with Supabase, R2, and OpenAI values. Keep this file local: it is ignored by Git and must not be committed.

For a new Supabase project, run the SQL migrations in `database/migrations/` in numeric order. If the database already has the initial schema, apply the later migrations once.

Start the API in one terminal:

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --env-file ../.env.local --host 127.0.0.1 --port 8000
```

Start the web app in a second terminal:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000), upload an `.htm`, `.html`, or `.pdf` filing, then wait for processing to finish before asking questions.

To view the preprocessed shared dataset, use the same Supabase and R2 environment that was used for ingestion. With a new environment, upload the filings again; raw filings in `Files/` are deliberately not versioned.

Useful checks:

```bash
PYTHONPATH=.:backend backend/.venv/bin/python -m pytest -q tests
cd frontend && npm run build
```

## 2. Technical architecture

```text
Next.js UI
    │
    ▼
FastAPI API ─── Supabase PostgreSQL + pgvector
    │                    ├─ documents, pages, sections, tables, XBRL facts
    │                    ├─ embeddings, chat topics, messages, evidence snapshots
    │                    └─ owner/document isolation
    ├── Cloudflare R2: original uploaded files
    └── OpenAI: embeddings and evidence-bounded answer generation
```

**Ingestion.** HTML and Inline XBRL are the primary path: the processor extracts pages, headings, tables, XBRL facts, and searchable chunks. Before persistence, an upload whose filename explicitly states a year, quarter, or SEC form is checked against the filing metadata. A known mismatch is rejected with an actionable message in the filing panel, before it reaches R2 or Supabase. PDFs have a text/page fallback. The original file is stored in R2; normalized, queryable data is stored in Supabase.

**Retrieval and answers.** Every chat topic belongs to one filing. The API derives the active `document_id` from that topic, then combines lexical search, vector search, statement tables, and XBRL facts only from that filing. Direct metrics and supported calculations—including growth, cross-statement metrics, organic-growth bridges, explicit zero values, and inventory turnover—use filing-derived values and preserve the requested period and units. The answer layer receives ranked evidence, cites it as `[S1]`, `[S2]`, and returns `Not found in this filing.` when the evidence is insufficient. It does not attach sources to abstentions.

**Benchmark discipline.** The included 136-question set is an offline evaluator only. It records answer, cited-page, latency, and failure data for quality work; expected answers are never injected into live chat responses.

**Core API contracts.** All endpoints are rooted at `/v1`:

- `POST /documents` uploads a filing; `GET /documents` lists it; `GET /documents/{id}/status` reports processing progress.
- `POST /chat-topics`, `GET /chat-topics`, `PATCH /chat-topics/{id}`, and `DELETE /chat-topics/{id}` create, list, rename, and remove chats. Deleting a chat preserves the filing and its processed data.
- `POST /chat-topics/{id}/messages` asks a question and returns the user message, assistant message, and evidence. `GET /messages/{id}/evidence` and `GET /documents/{id}/pages/{page}` expose the linked evidence.

Request/response schemas and examples are in [docs/api-contracts.md](docs/api-contracts.md). The build plan and product decisions are in [docs/implementation-plan.md](docs/implementation-plan.md) and [docs/approach-note.md](docs/approach-note.md).

## 3. Feature and UI highlights

- Upload a filing once and reuse it across focused chat topics.
- Catch known filename/content filing mismatches before upload, explain the
  expected versus embedded filing identity in the left panel, and avoid storing
  invalid uploads.
- A filing card opens its latest chat; individual topics can be renamed or deleted without deleting the processed filing.
- Document-scoped context: switching a topic switches the active filing and its retrieval boundary.
- Clear upload and multi-stage processing feedback for reading, section/table extraction, Inline XBRL extraction, and search indexing.
- Chat UI includes timestamps, user-first messages, animated thinking state, auto-scroll to the newest message, and a fixed composer.
- Supported answers include compact links such as `Page 64 · Section 2 +2 more`.
- The evidence popup presents supporting sources sequentially and can open the complete extracted filing page.
- Source formatting distinguishes narrative excerpts, tables, and Inline XBRL facts for readability.
- Filing-grounded calculation answers show their inputs and result, while the
  evidence link retains each supporting filing source.
- Server-side owner and document filtering prevents cross-filing retrieval; insufficient evidence produces an explicit abstention instead of a guessed answer.
- The filing panel includes the submission attribution for the VRIZE AI Hackathon.
