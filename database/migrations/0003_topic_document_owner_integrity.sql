-- The API always validates the topic's owner and document before retrieval.
-- Enforce the same invariant in PostgreSQL so a service-role write cannot
-- accidentally create a topic that points at another owner's filing.
alter table public.documents
  drop constraint if exists documents_id_owner_id_key;

alter table public.documents
  add constraint documents_id_owner_id_key unique (id, owner_id);

alter table public.chat_topics
  drop constraint if exists chat_topics_document_owner_id_fkey;

alter table public.chat_topics
  add constraint chat_topics_document_owner_id_fkey
  foreign key (document_id, owner_id)
  references public.documents(id, owner_id)
  on delete restrict;
