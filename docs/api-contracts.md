# API contracts — filing-scoped MVP

Production endpoints identify the user from the Supabase bearer token. The
client never sends an owner ID, and it never supplies a retrieval document ID
when asking a chat question. Local development may use the explicitly
configured demo owner fallback; that fallback is not a production contract.

## Implemented endpoints

- `POST /v1/documents` accepts one HTML/Inline XBRL or PDF filing. The server
  hashes the file, compares any year/quarter/form encoded in its filename with
  the filing's own metadata, reuses an owner-scoped duplicate when present,
  saves the original in R2, creates a processing job, and returns `202
  Accepted`. A known identity mismatch returns `422 Unprocessable Entity`
  before R2 or database persistence. HTML/Inline XBRL is the rich ingestion
  path; PDF has a page-text fallback only (no OCR, table extraction, or XBRL
  facts).
- `GET /v1/documents` lists the caller's persistent filing library, newest
  first, without storage keys, hashes, or credentials.
- `GET /v1/documents/{document_id}/status` returns the owner-scoped document
  state and the latest job's `stage`, `progress`, and `status`.
- `POST /v1/documents/{document_id}/retry` starts a fresh processing job for
  an owner-owned ready or failed filing without uploading a second original.
- `GET /v1/documents/{document_id}/pages/{page_number}` returns one
  owner-authorized, extracted source page for the evidence viewer.
- `GET /v1/chat-topics` lists the caller's topics with their filing display
  metadata.
- `POST /v1/chat-topics` creates another renameable topic for an existing,
  owner-owned filing.
- `PATCH /v1/chat-topics/{topic_id}` renames a topic only; its filing is
  immutable.
- `GET /v1/chat-topics/{topic_id}/messages` returns ordered message history.
- `POST /v1/chat-topics/{topic_id}/messages` resolves the topic and its filing
  on the server, then returns one completed JSON answer.
- `GET /v1/messages/{message_id}/evidence` returns the owner-authorized,
  ordered snapshot evidence used by a completed assistant answer.

There is no SSE, WebSocket, or token-streaming answer endpoint today. Upload
processing is asynchronous and is observed by polling the status endpoint;
question answering is a non-streaming request/response operation. The UI's
thinking animation must therefore be treated as client-side progress feedback,
not as an authoritative server stream.

## Current request and response shapes

### `POST /v1/documents`

The request is `multipart/form-data` with a `file` field. A successful queued
upload (or duplicate reuse) returns a document and its selected chat topic.

```json
{
  "document": {
    "id": "document UUID",
    "original_filename": "JPMORGAN_2022Q2_10Q.htm",
    "status": "queued",
    "size_bytes": 1432871
  },
  "chat_topic": {
    "id": "topic UUID",
    "document_id": "document UUID",
    "title": "JPMORGAN_2022Q2_10Q"
  },
  "deduplicated": false
}
```

If the filename explicitly identifies a filing but its embedded metadata
disagrees, the server returns `422` with an actionable `detail`, for example:

```json
{
  "detail": "Incorrect file. Based on the filename \u201c3M_2023Q2_10Q.htm\u201d, expected FY2023 Q2 Form 10-Q, but the file contains FY2023 Q1 Form 10-Q (period ended March 31, 2023) instead. Upload the expected filing or rename the file to match its contents."
}
```

### `GET /v1/documents/{document_id}/status`

```json
{
  "document": {
    "id": "document UUID",
    "status": "processing",
    "processing_error": null,
    "processed_at": null
  },
  "job": {
    "status": "processing",
    "stage": "reading_filing",
    "progress": 20
  }
}
```

`stage` is a best-effort, persisted processing stage. Expected values currently
include `queued`, `reading_filing`, `extracting_sections`, `extracting_tables`,
`extracting_xbrl`, `building_search_index`, `complete`, and `failed`; callers
must treat unfamiliar future stage names as displayable text.

### `POST /v1/chat-topics/{topic_id}/messages`

The body is `{ "content": "plain-English question" }`. The successful `201`
response is completed (not streamed):

```json
{
  "user_message": {
    "id": "message UUID",
    "role": "user",
    "content": "What was total net revenue?",
    "created_at": "2026-08-23T14:00:00Z"
  },
  "assistant_message": {
    "id": "message UUID",
    "role": "assistant",
    "content": "…",
    "answer_status": "supported",
    "created_at": "2026-08-23T14:00:02Z"
  },
  "evidence": []
}
```

`answer_status` is `supported`, `not_found`, or `failed`. A `not_found` answer
uses the product copy `Not found in this filing.` and has no source links.

## Source and evidence endpoint shapes

- `GET /v1/documents`

  Returns the caller's persistent filing library, newest first. Each item will
  include `id`, `original_filename`, filing metadata, status, safe processing
  error, and timestamps. It will never return `storage_key`, R2 credentials,
  hashes, or another owner's filing.

- `GET /v1/documents/{document_id}/pages/{page_number}`

  Returns one owner-scoped, extracted source page:

  ```json
  {
    "document_id": "document UUID",
    "page_number": 64,
    "source_anchor": "#page-64",
    "content": "Extracted filing page text"
  }
  ```

  This is extracted text plus an anchor, not an R2 public URL. The browser
  never receives direct R2 credentials.

- `GET /v1/messages/{message_id}/evidence`

  Returns the evidence in the exact order shown for a completed assistant
  answer. New evidence rows persist a display snapshot so history still shows
  the original page/heading/table context after a filing is reprocessed:

  ```json
  [
    {
      "ordinal": 1,
      "excerpt": "…",
      "page_number": 64,
      "section_heading": "Consolidated Statements of Income",
      "source_anchor": "#income-statement",
      "source_type": "table",
      "table_id": "table UUID",
      "table_title": "Consolidated Statements of Income"
    }
  ]
  ```

  Snapshot fields are nullable for messages written before migration `0002`.
  Clients should fall back to the embedded current section metadata for those
  older records.

## Invariants

1. Original files live only in R2; Supabase stores filing metadata, extracted
   pages/sections/tables/XBRL facts, embeddings, topics, messages, and evidence
   snapshots.
2. `documents.owner_id` is authoritative. The server validates document or
   topic ownership before every content, page, retrieval, or evidence read,
   and migration `0003` enforces that a topic's `owner_id` matches its
   referenced document at the database layer.
3. A topic permanently references one document. Switching topic changes the
   only eligible filing context on the server.
4. A filing can have multiple topics. Uploading a duplicate reuses the filing
   rather than creating a second stored original.
5. Every supported answer has ordered evidence. The client renders a compact
   source summary and can expand the complete evidence sequence.
