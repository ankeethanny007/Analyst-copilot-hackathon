# BRD realignment — evidence-first corrective release

## Product contract retained

Analyst Copilot answers one filing at a time. A supported answer must have
page-addressable evidence from that filing; an answer without sufficient
support is exactly `Not found in this filing.` A filing is stored once in R2
and can have many persistent, renameable chat topics. Switching topics changes
the only document the server may retrieve from.

## Corrections in this release

- The practice-question answer key is no longer consulted by the live chat
  route. It is offline evaluation data only.
- Retrieval now combines all ordered sections, optional semantic chunk matches,
  filing tables, and page-addressable Inline XBRL facts. A missing embedding
  service falls back to lexical/table/XBRL retrieval instead of turning a
  usable filing into a failed or false-absence answer.
- Inline XBRL contexts are read before the non-rendered header is removed.
  Numeric fact scale/sign/normalized values and period dates are persisted on
  reprocessing.
- Exact evidence display metadata is snapshotted with each assistant message;
  history does not reconstruct an old table source from a later page heading.
- The product has a filing library, nested document-scoped topics, new-chat
  controls, upload/processing stages, readiness gating, retry processing, and
  a compact evidence popup.
- HTML/Inline XBRL remains first. PDF now has a page-text fallback with no OCR
  or invented table/XBRL structure.
- A benchmark runner scores answer plus page location and writes failures with
  the returned answer, cited pages, status, and latency for diagnosis.

## Acceptance checks

1. A queued or processing filing cannot be queried; the UI polls its persisted
   job state and enables the composer only at `ready`.
2. A supported message has one or more valid `[S#]` labels and persists only
   cited source snapshots. An abstention has no source link.
3. Every retrieval query begins after the server resolves topic → owner →
   document. Database migration `0003` also prevents a chat topic from
   referencing a different owner's filing.
4. A reprocess replaces derived pages/sections/tables/facts/chunks without
   uploading another original file. Snapshot evidence remains readable.
5. The benchmark may claim a score only for filings that have been uploaded,
   processed, and evaluated—not for the answer key itself.

## Required rollout and honest limits

Run migrations `0002` and `0003`, then retry existing filings once. The full
136-question score cannot be claimed until all 78 supplied filings are in the
filing library; this repository only contains the JPMorgan sample. The system
will still abstain when a question is unsupported, but it should no longer
abstain merely because indexing is incomplete, an embedding request fails, or
the relevant fact only exists in a table/XBRL record.
