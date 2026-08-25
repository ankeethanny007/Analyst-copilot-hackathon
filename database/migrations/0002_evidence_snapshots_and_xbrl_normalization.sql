-- Preserve the exact evidence presentation used when an answer was generated.
-- The referenced section/table can later be reprocessed or deleted, so the
-- page, heading, anchor and table context shown in message history must not
-- depend exclusively on a live join back to the processed filing tables.
alter table public.message_evidence
  add column if not exists page_number integer,
  add column if not exists section_heading text,
  add column if not exists source_anchor text,
  add column if not exists source_type text,
  add column if not exists table_id uuid references public.document_tables(id) on delete set null,
  add column if not exists table_title text;

-- Backfill older evidence while its current section/chunk joins are available.
-- This preserves useful source labels for existing chats before a later
-- reprocessing run replaces their extracted sections.
update public.message_evidence as evidence
set
  page_number = coalesce(evidence.page_number, section.page_number),
  section_heading = coalesce(evidence.section_heading, section.heading),
  source_anchor = coalesce(evidence.source_anchor, section.source_anchor),
  source_type = coalesce(
    evidence.source_type,
    (select chunk.content_type from public.document_chunks as chunk where chunk.id = evidence.chunk_id),
    'narrative'
  )
from public.document_sections as section
where evidence.section_id = section.id
  and (
    evidence.page_number is null
    or evidence.section_heading is null
    or evidence.source_anchor is null
    or evidence.source_type is null
  );

-- Snapshot fields make it safe to clear and rebuild a filing's processed
-- content.  The source relation remains useful when it exists, but no longer
-- prevents a reprocessing job from removing obsolete sections.
alter table public.message_evidence
  alter column section_id drop not null;

alter table public.message_evidence
  drop constraint if exists message_evidence_section_id_fkey;

alter table public.message_evidence
  add constraint message_evidence_section_id_fkey
  foreign key (section_id) references public.document_sections(id) on delete set null;

alter table public.message_evidence
  drop constraint if exists message_evidence_snapshot_page_number_positive;

alter table public.message_evidence
  add constraint message_evidence_snapshot_page_number_positive
  check (page_number is null or page_number > 0);

-- Inline XBRL commonly records display scale and sign independently from the
-- raw text.  Keep the normalized numeric value nullable because non-numeric
-- facts (for example entity names) are still useful as evidence.
alter table public.xbrl_facts
  add column if not exists scale text,
  add column if not exists sign text,
  add column if not exists normalized_value numeric;

create index if not exists message_evidence_message_ordinal_idx
  on public.message_evidence(message_id, ordinal);

create index if not exists xbrl_facts_document_period_idx
  on public.xbrl_facts(document_id, period_end, instant_date);
