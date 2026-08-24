create extension if not exists vector;
create extension if not exists pgcrypto;

create type document_status as enum ('uploaded','queued','processing','ready','failed');
create type message_role as enum ('user','assistant');
create type answer_status as enum ('supported','not_found','failed');

create table public.documents (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  original_filename text not null,
  media_type text not null check (media_type in ('text/html','application/xhtml+xml','application/pdf')),
  sha256 char(64) not null,
  storage_key text not null unique,
  company_name text,
  filing_type text,
  filing_period_end date,
  status document_status not null default 'uploaded',
  processing_error text,
  created_at timestamptz not null default now(),
  processed_at timestamptz,
  unique (owner_id, sha256)
);

create table public.chat_topics (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  document_id uuid not null references public.documents(id) on delete restrict,
  title text not null check (char_length(title) between 1 and 120),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.document_pages (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  page_number integer not null check (page_number > 0),
  source_anchor text,
  content text not null,
  unique (document_id, page_number)
);

create table public.document_sections (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  page_id uuid references public.document_pages(id) on delete set null,
  page_number integer not null check (page_number > 0),
  ordinal integer not null,
  heading text,
  content text not null,
  source_anchor text,
  unique (document_id, ordinal)
);

create table public.document_tables (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  section_id uuid references public.document_sections(id) on delete set null,
  page_number integer not null check (page_number > 0),
  title text,
  content jsonb not null,
  source_anchor text
);

create table public.xbrl_facts (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  section_id uuid references public.document_sections(id) on delete set null,
  concept text not null,
  context_ref text,
  value text not null,
  unit text,
  decimals text,
  period_start date,
  period_end date,
  instant_date date,
  page_number integer,
  source_anchor text
);

create table public.document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  section_id uuid references public.document_sections(id) on delete set null,
  page_number integer not null check (page_number > 0),
  content text not null,
  content_type text not null check (content_type in ('narrative','table','xbrl')),
  embedding vector(1536),
  created_at timestamptz not null default now()
);

create index document_chunks_document_id_idx on public.document_chunks(document_id);
create index document_chunks_embedding_idx on public.document_chunks using hnsw (embedding vector_cosine_ops);
create index sections_document_page_idx on public.document_sections(document_id, page_number);
create index xbrl_facts_document_concept_idx on public.xbrl_facts(document_id, concept);

create table public.messages (
  id uuid primary key default gen_random_uuid(),
  chat_topic_id uuid not null references public.chat_topics(id) on delete cascade,
  role message_role not null,
  content text not null,
  answer_status answer_status,
  created_at timestamptz not null default now()
);

create table public.message_evidence (
  message_id uuid not null references public.messages(id) on delete cascade,
  ordinal integer not null,
  section_id uuid not null references public.document_sections(id) on delete restrict,
  chunk_id uuid references public.document_chunks(id) on delete set null,
  excerpt text not null,
  primary key (message_id, ordinal)
);

create table public.processing_jobs (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  status document_status not null default 'queued',
  stage text not null default 'queued',
  progress smallint not null default 0 check (progress between 0 and 100),
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.documents enable row level security;
alter table public.chat_topics enable row level security;
alter table public.document_pages enable row level security;
alter table public.document_sections enable row level security;
alter table public.document_tables enable row level security;
alter table public.xbrl_facts enable row level security;
alter table public.document_chunks enable row level security;
alter table public.messages enable row level security;
alter table public.message_evidence enable row level security;
alter table public.processing_jobs enable row level security;

create policy "users access their documents" on public.documents for all using (auth.uid() = owner_id) with check (auth.uid() = owner_id);
create policy "users access their topics" on public.chat_topics for all using (auth.uid() = owner_id) with check (auth.uid() = owner_id);
create policy "users access topic messages" on public.messages for all using (exists (select 1 from public.chat_topics t where t.id = chat_topic_id and t.owner_id = auth.uid()));

-- Processed evidence is served by the backend with the service role after topic ownership
-- checks. No direct browser policy is deliberately granted for these tables.

-- Retrieval RPC: document ID is supplied only after server-side ownership/topic validation.
create or replace function public.match_document_chunks(p_document_id uuid, p_embedding vector(1536), p_limit integer default 12)
returns table (id uuid, section_id uuid, page_number integer, content text, similarity float)
language sql stable as $$
  select id, section_id, page_number, content, 1 - (embedding <=> p_embedding) as similarity
  from public.document_chunks
  where document_id = p_document_id and embedding is not null
  order by embedding <=> p_embedding
  limit p_limit;
$$;
