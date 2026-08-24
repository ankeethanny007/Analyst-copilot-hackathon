# Approach note

We optimize for the scoring rule: a correct answer requires its correct location; an unproven answer is worse than abstaining. The first pipeline targets SEC HTML/Inline XBRL because it preserves text, financial facts and document structure. It builds page- and section-addressable evidence, then embeddings for semantic search. PDF ingestion is a narrower, page-text fallback rather than the primary route.

The database holds metadata and processed material; R2 holds original filings. Hash-based reuse prevents re-uploading and reprocessing the same owner file. Chats are lightweight, renameable references to an immutable document ID. The server, not the browser or prompt, resolves each topic and constrains all retrieval to its document ID. Answers are stored with an ordered, exact evidence set so the UI can show a compact source link and a sequential evidence popup. When the validator cannot establish support, the system responds: “Not found in this filing.”
