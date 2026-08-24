# Five-day MVP plan

## Scope locked in

The MVP answers questions over one filing at a time. It stores each unique owner filing once, supports many persistent and renameable chat topics per filing, uses exact page/section evidence, and abstains where evidence cannot prove an answer. It does not include AWS, Azure, OCR-first ingestion, multi-document reasoning, collaboration, billing, or a queueing platform.

## Day 1 — foundation and upload

- [x] Monorepo folders, environment template, API contract, schema, supplied sample and approach note.
- [x] Initial Next.js upload/chat shell with upload and processing states.
- [x] FastAPI contract shell with safe file type validation.
- [ ] Configure Supabase project, apply migration, and enable auth.
- [ ] Configure `analyst-copilot-files` R2 bucket; implement SHA-256, upload, owner/hash deduplication, and processing job creation.
- [ ] Replace contract responses with Supabase/R2 persistence; test upload → document → auto-created topic.

## Day 2 — intelligence

- [x] Extract HTML/Inline XBRL first: page breaks, page addresses, tables, facts, anchors, and chunks. JPMorgan sample regression test validates the pipeline.
- PDF fallback: text/page extraction only; OCR deferred.
- Persist pages, sections, tables, XBRL facts and embeddings; show stage-based progress.

## Day 3 — answer system

- Resolve topic → owner → document server-side before any retrieval.
- Retrieve chunks and XBRL facts only for that document; rerank.
- Validate evidence, calculate deterministically where needed, then generate answer.
- Persist ordered evidence or return `Not found in this filing.`

## Day 4 — product UX

- Topic switch, rename, message timestamps, motion, empty/error states and AI status animation.
- Evidence link: `Page 64 · Section 2 +2 more`; modal renders the ordered exact excerpts, then full-page action.

## Day 5 — quality and demo

- Run the included 136-question `sample-data/practice-questions.jsonl` benchmark and categorize answer, retrieval, location, calculation and abstention defects.
- Match each test row to its `doc_name`; only score a question when that filing has been ingested. Compare answer and exact `evidence_page_num`, then retain failures with retrieved excerpts for diagnosis.
- Test owner isolation and cross-topic/cross-document leakage on every route.
- Stabilize a deterministic demo: upload → ready → ask → sources → switch → history → rename.
