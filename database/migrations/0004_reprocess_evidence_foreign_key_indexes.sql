-- Reprocessing replaces a filing's derived sections and tables.  These
-- foreign keys use ON DELETE SET NULL so historical chat evidence retains its
-- snapshot fields, but PostgreSQL otherwise scans every evidence row while
-- applying that update.  The indexes keep a filing retry bounded as the chat
-- history grows.
create index if not exists message_evidence_section_id_idx
  on public.message_evidence(section_id);

create index if not exists message_evidence_table_id_idx
  on public.message_evidence(table_id);
