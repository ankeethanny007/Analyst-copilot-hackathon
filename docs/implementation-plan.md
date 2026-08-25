# Five-day MVP plan

## Scope locked in

The MVP answers questions over one filing at a time. It stores each unique owner filing once, supports many persistent and renameable chat topics per filing, uses exact page/section evidence, and abstains where evidence cannot prove an answer. It does not include AWS, Azure, OCR-first ingestion, multi-document reasoning, collaboration, billing, or a queueing platform.

## Day 1 — foundation and upload

- [x] Monorepo folders, environment template, API contract, schema, supplied sample and approach note.
- [x] Initial Next.js upload/chat shell with upload and processing states.
- [x] FastAPI contract shell with safe file type validation.
- [x] Supabase/R2 backend integration, SHA-256 owner/hash reuse, processing-job creation, and upload → document → auto-created topic flow.
- [x] Apply the additive live migrations `0002` and `0003`, then retry existing filings once.

## Day 2 — intelligence

- [x] Extract HTML/Inline XBRL first: page breaks, page addresses, tables, facts, anchors, and chunks. JPMorgan sample regression test validates the pipeline.
- PDF fallback: text/page extraction only; OCR deferred.
- [x] Persist pages, sections, tables, normalized XBRL facts and embeddings; show stage-based progress. A lexical/table/XBRL path remains available when embeddings are unavailable.

## Day 3 — answer system

- [x] Resolve topic → owner → document server-side before any retrieval; a database constraint also prevents cross-owner topic/document links.
- [x] Retrieve lexical sections, optional semantic chunks, statement tables, and question-relevant XBRL facts only for that document; rerank them.
- [x] Validate evidence, calculate direct metrics/growth deterministically where possible, then use the model only within the supplied evidence boundary.
- [x] Persist ordered source snapshots or return `Not found in this filing.` Only insufficient evidence—not an empty model-payload parsing bug or unavailable embedding—may produce an abstention.

## Day 4 — product UX

- [x] Topic switch, rename, message timestamps, motion, empty/error states and AI status animation.
- [x] Evidence link: `Page 64 · Section 2 +2 more`; modal renders the ordered exact excerpts and can open the stored full page.

## Day 5 — quality and demo

- [x] Add an offline runner for the included 136-question `sample-data/practice-questions.jsonl` benchmark; it records answer, cited pages, latency, and failures without exposing the answer key to chat.
- [ ] Load/process every benchmark filing before claiming a full score. The current repo contains only the JPMorgan sample; the current live library has only the filings already uploaded by the user.
- [x] Test owner-scoped document/page/evidence reads and topic/document binding in the API regression suite.
- [x] Stabilize the demo flow: upload → ready → ask → sources → full page → switch → history → rename.
