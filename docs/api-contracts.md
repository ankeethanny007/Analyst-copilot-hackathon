# API contracts — Day 1 freeze

All authenticated endpoints identify the user from the Supabase bearer token. The client never sends an owner ID or a retrieval document ID.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/documents` | Multipart upload. Server hashes the file; an owner/hash duplicate returns its existing document and a new or selected topic. Otherwise server uploads to R2 and queues processing. |
| GET | `/v1/documents` | Owner’s persistent filings and processing state. |
| GET | `/v1/documents/{id}/status` | Job stage, percentage, and any safe error. |
| POST | `/v1/chat-topics` | Create a renameable topic for an existing owner-owned document. |
| GET | `/v1/chat-topics` | Topics with document display metadata. |
| PATCH | `/v1/chat-topics/{id}` | Rename only; the topic’s document relationship is immutable. |
| GET | `/v1/chat-topics/{id}/messages` | Topic history. |
| POST | `/v1/chat-topics/{id}/messages` | Ask a document-scoped question; returns streamed status then a supported answer or abstention. |
| GET | `/v1/messages/{id}/evidence` | Ordered evidence sections used by that answer. |

## Answer shape

```json
{
  "id": "message UUID",
  "status": "supported",
  "content": "Markets revenue was …",
  "created_at": "2026-08-23T14:00:00Z",
  "source_summary": "Page 64 · Markets revenue +2 more",
  "evidence_count": 3
}
```

`status: "not_found"` always uses the exact product copy: `Not found in this filing.` It has no evidence links.

## Invariants

1. Original files live only in R2; Supabase stores metadata and processed evidence.
2. `documents.owner_id` is authoritative. Every child document query is joined to an owner-owned document.
3. A topic permanently references one document. Switching topic changes context because the server resolves the topic first.
4. Evidence is persisted as an ordered set of exact sections; the client displays it sequentially in its popup.
