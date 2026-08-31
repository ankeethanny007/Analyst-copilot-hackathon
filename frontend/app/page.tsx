"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Evidence = {
  ordinal?: number;
  excerpt: string;
  page_number?: number | null;
  heading?: string | null;
  section_heading?: string | null;
  table_title?: string | null;
  source_anchor?: string | null;
  source_type?: string | null;
  document_sections?:
    | { page_number?: number | null; heading?: string | null; source_anchor?: string | null }
    | Array<{ page_number?: number | null; heading?: string | null; source_anchor?: string | null }>
    | null;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  answer_status?: string | null;
  created_at: string;
  message_evidence?: Evidence[];
};

type TopicDocument = {
  id?: string;
  original_filename?: string;
  status?: string;
};

type Topic = {
  id: string;
  title: string;
  document_id: string;
  created_at?: string;
  updated_at?: string;
  documents?: TopicDocument | TopicDocument[] | null;
};

type Filing = {
  id: string;
  original_filename: string;
  status: string;
  created_at?: string;
  processed_at?: string | null;
  processing_error?: string | null;
  size_bytes?: number;
};

type ProcessingJob = {
  status?: string;
  stage?: string;
  progress?: number | null;
  error?: string | null;
};

type ProcessingSnapshot = {
  document: Partial<Filing> & { id?: string };
  job?: ProcessingJob | null;
};

type SourceModal = {
  evidence: Evidence[];
  documentId: string;
};

type SourcePage = {
  pageNumber: number;
  content?: string;
  loading: boolean;
  error?: string;
};

type FilingWithTopics = Filing & { topics: Topic[] };

const PROCESSING_STEPS = ["queued", "reading_filing", "extracting_sections", "extracting_tables", "extracting_xbrl", "building_search_index", "complete"];

const STAGE_COPY: Record<string, string> = {
  queued: "Queued for processing",
  reading_filing: "Reading filing",
  extracting_sections: "Extracting sections",
  extracting_tables: "Extracting tables",
  extracting_xbrl: "Extracting Inline XBRL",
  building_search_index: "Building search index",
  complete: "Filing is ready",
  failed: "Processing needs attention",
};

function displayTime(value: string | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    day: "numeric",
  }).format(date);
}

function topicDocument(topic: Topic): TopicDocument | undefined {
  return Array.isArray(topic.documents) ? topic.documents[0] : topic.documents ?? undefined;
}

function usableEvidenceHeading(value: string | null | undefined) {
  const cleaned = value?.replace(/\s+/g, " ").trim();
  if (!cleaned) return undefined;
  // EDGAR can split the persistent navigation label into individual inline
  // spans, producing labels such as `T able of Contents` on substantive
  // pages. It is never useful evidence context, so use the neutral fallback
  // below instead of displaying it in a compact source link or modal.
  const compact = cleaned.toLowerCase().replace(/[^a-z0-9]+/g, "");
  if (compact === "tableofcontents" || /^page\s+\d+$/i.test(cleaned)) return undefined;
  return cleaned;
}

function evidenceLocation(evidence: Evidence) {
  const section = Array.isArray(evidence.document_sections) ? evidence.document_sections[0] : evidence.document_sections;
  return {
    page: section?.page_number ?? evidence.page_number ?? undefined,
    // New evidence rows snapshot their original table/section label. Prefer
    // that immutable label so a reprocessed filing cannot turn old sources
    // into a generic page heading after a chat reload.
    heading: [evidence.table_title, evidence.section_heading, section?.heading, evidence.heading]
      .map(usableEvidenceHeading)
      .find(Boolean) ?? "Filing section",
  };
}

function evidenceLabel(evidence: Evidence[]) {
  const first = evidenceLocation(evidence[0]);
  const location = first.page ? `Page ${first.page}` : "Filing evidence";
  return `${location} · ${first.heading}${evidence.length > 1 ? ` +${evidence.length - 1} more` : ""}`;
}

function evidenceKind(evidence: Evidence) {
  if (evidence.source_type === "xbrl") return "Inline XBRL fact";
  if (evidence.source_type === "table" || evidence.table_title) return "Table excerpt";
  return "Filing excerpt";
}

function cleanedExcerpt(value: string) {
  return value
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function tableRows(excerpt: string) {
  // Table excerpts are persisted as plain text so that they remain durable
  // across reprocessing.  Older excerpts have their original newlines
  // compacted by retrieval; restore only visual row breaks at a clearly new
  // row label.  The underlying labels and values are never changed.
  const rowText = cleanedExcerpt(excerpt)
    .replace(/\b((?:19|20)\d{2})\s+(?=[A-Z][^|]{1,90}\s*\|)/g, "$1\n")
    .replace(/((?:[$€£¥]?\(?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?\)?))\s+(?=[A-Z][^|]{1,90}\s*\|)/g, "$1\n");

  return rowText
    .split(/\n+/)
    .map(row => row
      .split(/\s*\|\s*/)
      .map(cell => cell.trim())
      .filter(Boolean))
    .filter(row => row.length > 0)
    .map(row => row.reduce<string[]>((cells, cell) => {
      // EDGAR tables often store a currency symbol in its own cell.  Joining
      // it to the following numeric cell makes the rendered value legible
      // without changing either value.
      if (/^[€£¥$]$/.test(cell)) {
        cells.push(cell);
        return cells;
      }
      if (cells.length && /^[€£¥$]$/.test(cells[cells.length - 1])) {
        cells[cells.length - 1] = `${cells[cells.length - 1]}${cell}`;
        return cells;
      }
      cells.push(cell);
      return cells;
    }, []));
}

function narrativeParagraphs(excerpt: string) {
  return cleanedExcerpt(excerpt)
    .split(/\n{2,}/)
    .map(paragraph => paragraph.trim())
    .filter(Boolean);
}

function inlineXbrlFields(excerpt: string) {
  return cleanedExcerpt(excerpt)
    .split(/\s*\|\s*/)
    .map(field => {
      const colon = field.indexOf(":");
      return colon > 0
        ? { label: field.slice(0, colon).trim(), value: field.slice(colon + 1).trim() }
        : { label: "Evidence", value: field.trim() };
    })
    .filter(field => field.value);
}

function EvidenceExcerpt({ evidence }: { evidence: Evidence }) {
  const kind = evidenceKind(evidence);

  if (kind === "Table excerpt") {
    const rows = tableRows(evidence.excerpt);
    return (
      <div className="evidence-excerpt evidence-table-excerpt">
        <span className="evidence-kind">{kind}</span>
        <div className="evidence-table" role="table" aria-label="Extracted filing table">
          {rows.map((row, rowIndex) => (
            <div className="evidence-table-row" role="row" key={`${rowIndex}-${row.join("-").slice(0, 36)}`}>
              {row.map((cell, cellIndex) => <span className="evidence-table-cell" role="cell" key={`${cellIndex}-${cell.slice(0, 24)}`}>{cell}</span>)}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (kind === "Inline XBRL fact") {
    return (
      <div className="evidence-excerpt evidence-fact-excerpt">
        <span className="evidence-kind">{kind}</span>
        <dl className="evidence-fact-list">
          {inlineXbrlFields(evidence.excerpt).map((field, index) => (
            <div key={`${field.label}-${index}`}>
              <dt>{field.label}</dt>
              <dd>{field.value}</dd>
            </div>
          ))}
        </dl>
      </div>
    );
  }

  return (
    <div className="evidence-excerpt evidence-narrative-excerpt">
      <span className="evidence-kind">{kind}</span>
      {narrativeParagraphs(evidence.excerpt).map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 36)}`}>{paragraph}</p>)}
    </div>
  );
}

function citedEvidence(message: Message) {
  const evidence = message.message_evidence ?? [];
  if (message.role !== "assistant" || message.answer_status !== "supported" || !evidence.length) return [];

  const citationOrdinals = new Set(
    [...message.content.matchAll(/\[S(\d+)\]/g)].map(match => Number(match[1])),
  );
  if (!citationOrdinals.size) return [];
  return evidence.filter((item, index) => citationOrdinals.has(item.ordinal ?? index + 1));
}

function normalizeDocuments(value: unknown): Filing[] {
  const candidates = Array.isArray(value)
    ? value
    : value && typeof value === "object" && Array.isArray((value as { documents?: unknown }).documents)
      ? (value as { documents: unknown[] }).documents
      : [];
  return candidates.filter((item): item is Filing => Boolean(item && typeof item === "object" && "id" in item && "original_filename" in item));
}

function stageIndex(stage: string | undefined, status: string | undefined) {
  if (status === "ready") return PROCESSING_STEPS.length - 1;
  if (status === "failed") return -1;
  const index = PROCESSING_STEPS.indexOf(stage ?? "");
  return index >= 0 ? index : 0;
}

function isBlocked(status: string | undefined) {
  return status === "queued" || status === "processing" || status === "failed";
}

function isPollingStatus(status: string | undefined) {
  return status === "queued" || status === "processing";
}

function statusText(status: string | undefined) {
  if (status === "ready") return "Ready";
  if (status === "failed") return "Needs attention";
  if (status === "processing") return "Processing";
  if (status === "queued") return "Queued";
  return "Available";
}

function statusClass(status: string | undefined) {
  if (status === "ready") return "is-ready";
  if (status === "failed") return "is-failed";
  if (status === "processing" || status === "queued") return "is-processing";
  return "";
}

function filenameStem(filename: string) {
  return filename.replace(/\.[^.]+$/, "");
}

function ProcessingTracker({
  filing,
  snapshot,
  onRetry,
  retrying,
}: {
  filing: Filing;
  snapshot?: ProcessingSnapshot;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const status = snapshot?.document.status ?? filing.status;
  const job = snapshot?.job;
  const stage = job?.stage ?? (status === "ready" ? "complete" : status);
  const progressFromJob = job?.progress;
  const progress = status === "ready" ? 100 : Math.max(5, Math.min(100, progressFromJob ?? (status === "queued" ? 10 : 25)));
  const currentStep = stageIndex(stage, status);
  const error = snapshot?.document.processing_error ?? filing.processing_error ?? job?.error;

  return (
    <div className={`processing-card ${status === "failed" ? "failed" : ""}`} role={status === "failed" ? "alert" : "status"}>
      <div className="processing-heading">
        <div>
          <span className={`status-dot ${statusClass(status)}`} aria-hidden="true" />
          <strong>{STAGE_COPY[stage ?? ""] ?? statusText(status)}</strong>
        </div>
        <span>{status === "failed" ? "Action needed" : `${Math.round(progress)}%`}</span>
      </div>
      {status !== "failed" && <div className="progress-track" aria-label={`${Math.round(progress)}% processed`}><span style={{ width: `${progress}%` }} /></div>}
      {status !== "failed" && (
        <ol className="processing-steps" aria-label="Filing processing stages">
          {PROCESSING_STEPS.slice(0, -1).map((step, index) => (
            <li className={index <= currentStep ? "complete" : ""} key={step}>{STAGE_COPY[step]}</li>
          ))}
        </ol>
      )}
      {status === "failed" && (
        <div className="processing-error">
          <p>{error || "We could not process this filing. Upload a supported HTML or Inline XBRL filing and try again."}</p>
          {onRetry && <button className="retry-processing-button" onClick={onRetry} disabled={retrying}>{retrying ? "Restarting…" : "Retry processing"}</button>}
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [documents, setDocuments] = useState<Filing[]>([]);
  const [activeTopicId, setActiveTopicId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [thinkingTopicId, setThinkingTopicId] = useState<string | null>(null);
  const [showSources, setShowSources] = useState<SourceModal | null>(null);
  const [sourcePage, setSourcePage] = useState<SourcePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [messageLoading, setMessageLoading] = useState(false);
  const [topicsLoading, setTopicsLoading] = useState(true);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [uploadingFileName, setUploadingFileName] = useState<string | null>(null);
  const [creatingDocumentId, setCreatingDocumentId] = useState<string | null>(null);
  const [deletingTopicId, setDeletingTopicId] = useState<string | null>(null);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(null);
  const [processing, setProcessing] = useState<Record<string, ProcessingSnapshot>>({});

  const messagesRef = useRef<HTMLDivElement>(null);
  const activeTopicIdRef = useRef<string | null>(null);
  const messageRequestIdRef = useRef(0);
  const messageControllerRef = useRef<AbortController | null>(null);

  useEffect(() => { activeTopicIdRef.current = activeTopicId; }, [activeTopicId]);

  const request = useCallback(async <T,>(path: string, options?: RequestInit): Promise<T> => {
    const headers = new Headers(options?.headers);
    if (options?.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(`${API}/v1${path}`, { ...options, headers });
    if (!response.ok) {
      const errorBody = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(errorBody?.detail ?? "Unable to reach Analyst Copilot.");
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }, []);

  const loadTopics = useCallback(async () => {
    setTopicsLoading(true);
    try {
      const data = await request<Topic[]>("/chat-topics");
      const safeTopics = Array.isArray(data) ? data : [];
      setTopics(safeTopics);
      setActiveTopicId(current => safeTopics.some(topic => topic.id === current) ? current : safeTopics[0]?.id ?? null);
      return safeTopics;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load chat topics.");
      return [];
    } finally {
      setTopicsLoading(false);
    }
  }, [request]);

  const loadDocuments = useCallback(async () => {
    setDocumentsLoading(true);
    try {
      const data = await request<unknown>("/documents");
      setDocuments(normalizeDocuments(data));
      setDocumentsError(null);
    } catch (reason) {
      setDocumentsError(reason instanceof Error ? reason.message : "The filing library is temporarily unavailable.");
    } finally {
      setDocumentsLoading(false);
    }
  }, [request]);

  const refreshDocumentStatus = useCallback(async (documentId: string) => {
    try {
      const snapshot = await request<ProcessingSnapshot>(`/documents/${documentId}/status`);
      setProcessing(current => ({ ...current, [documentId]: snapshot }));
      if (snapshot.document.status) {
        setDocuments(current => current.map(document => document.id === documentId ? { ...document, ...snapshot.document } : document));
      }
      return snapshot;
    } catch {
      return undefined;
    }
  }, [request]);

  const upsertDocument = useCallback((document: Filing) => {
    setDocuments(current => {
      const existing = current.find(item => item.id === document.id);
      return existing ? current.map(item => item.id === document.id ? { ...item, ...document } : item) : [document, ...current];
    });
  }, []);

  const upsertTopic = useCallback((topic: Topic) => {
    setTopics(current => {
      const existing = current.find(item => item.id === topic.id);
      return existing ? current.map(item => item.id === topic.id ? { ...item, ...topic } : item) : [topic, ...current];
    });
  }, []);

  const activeTopic = useMemo(() => topics.find(topic => topic.id === activeTopicId) ?? null, [activeTopicId, topics]);

  const filings = useMemo<FilingWithTopics[]>(() => {
    const byId = new Map<string, Filing>();
    for (const document of documents) byId.set(document.id, document);
    for (const topic of topics) {
      const relation = topicDocument(topic);
      if (!byId.has(topic.document_id)) {
        byId.set(topic.document_id, {
          id: topic.document_id,
          original_filename: relation?.original_filename ?? "Untitled filing",
          status: relation?.status ?? "unknown",
        });
      }
    }
    return Array.from(byId.values()).map(filing => ({
      ...filing,
      topics: topics.filter(topic => topic.document_id === filing.id),
    }));
  }, [documents, topics]);

  const activeFiling = useMemo(() => activeTopic ? filings.find(filing => filing.id === activeTopic.document_id) ?? null : null, [activeTopic, filings]);
  const activeSnapshot = activeFiling ? processing[activeFiling.id] : undefined;
  const activeStatus = activeSnapshot?.document.status ?? activeFiling?.status;
  const isActiveBlocked = isBlocked(activeStatus);
  const isThinking = Boolean(activeTopic && thinkingTopicId === activeTopic.id);

  const pollableDocumentKey = useMemo(() => filings
    .filter(filing => isPollingStatus(processing[filing.id]?.document.status ?? filing.status))
    .map(filing => filing.id)
    .sort()
    .join(","), [filings, processing]);

  const loadMessages = useCallback(async (topicId: string) => {
    const requestId = ++messageRequestIdRef.current;
    messageControllerRef.current?.abort();
    const controller = new AbortController();
    messageControllerRef.current = controller;
    setMessageLoading(true);
    setShowSources(null);
    setSourcePage(null);
    try {
      const data = await request<Message[]>(`/chat-topics/${topicId}/messages`, { signal: controller.signal });
      if (requestId === messageRequestIdRef.current && activeTopicIdRef.current === topicId) setMessages(Array.isArray(data) ? data : []);
    } catch (reason) {
      if ((reason as { name?: string }).name !== "AbortError" && requestId === messageRequestIdRef.current && activeTopicIdRef.current === topicId) {
        setMessages([]);
        setError(reason instanceof Error ? reason.message : "Unable to load messages.");
      }
    } finally {
      if (requestId === messageRequestIdRef.current) setMessageLoading(false);
    }
  }, [request]);

  useEffect(() => {
    void Promise.all([loadTopics(), loadDocuments()]);
  }, [loadDocuments, loadTopics]);

  useEffect(() => {
    if (!activeTopicId) {
      messageControllerRef.current?.abort();
      ++messageRequestIdRef.current;
      setMessages([]);
      setMessageLoading(false);
      return;
    }
    void loadMessages(activeTopicId);
  }, [activeTopicId, loadMessages]);

  useEffect(() => {
    if (!pollableDocumentKey) return;
    const ids = pollableDocumentKey.split(",").filter(Boolean);
    let cancelled = false;
    const poll = async () => {
      const snapshots = await Promise.all(ids.map(id => refreshDocumentStatus(id)));
      if (cancelled || !snapshots.some(snapshot => snapshot?.document.status === "ready" || snapshot?.document.status === "failed")) return;
      void loadDocuments();
      void loadTopics();
    };
    void poll();
    const interval = window.setInterval(() => { void poll(); }, 1800);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [loadDocuments, loadTopics, pollableDocumentKey, refreshDocumentStatus]);

  useEffect(() => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: "auto" });
  }, [activeTopicId, isThinking, messageLoading, messages]);

  async function ask(event: FormEvent) {
    event.preventDefault();
    const prompt = question.trim();
    const topicId = activeTopic?.id;
    if (!prompt || !topicId || isThinking || isActiveBlocked) return;

    const pendingId = `pending-${Date.now()}`;
    setQuestion("");
    setError(null);
    setMessages(current => [...current, { id: pendingId, role: "user", content: prompt, created_at: new Date().toISOString() }]);
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    setThinkingTopicId(topicId);

    try {
      const result = await request<{ user_message: Message; assistant_message: Message; evidence: Evidence[] }>(`/chat-topics/${topicId}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: prompt }),
      });
      if (activeTopicIdRef.current === topicId) {
        const assistant: Message = {
          ...result.assistant_message,
          message_evidence: (result.evidence ?? []).map((item, index) => ({ ...item, ordinal: index + 1 })),
        };
        setMessages(current => [...current.filter(message => message.id !== pendingId), result.user_message, assistant]);
      }
      void loadTopics();
    } catch (reason) {
      if (activeTopicIdRef.current === topicId) {
        setMessages(current => current.filter(message => message.id !== pendingId));
        setQuestion(prompt);
        setError(reason instanceof Error ? reason.message : "Unable to answer this question.");
      }
    } finally {
      setThinkingTopicId(current => current === topicId ? null : current);
    }
  }

  async function renameTopic() {
    if (!activeTopic) return;
    const title = window.prompt("Rename chat", activeTopic.title)?.trim();
    if (!title || title === activeTopic.title) return;
    try {
      const updated = await request<Topic>(`/chat-topics/${activeTopic.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
      upsertTopic(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to rename chat.");
    }
  }

  async function deleteTopic(topic: Topic) {
    if (!window.confirm(`Delete the chat “${topic.title}”? The filing and its processed evidence will remain available.`)) return;
    setDeletingTopicId(topic.id);
    setError(null);
    try {
      await request<void>(`/chat-topics/${topic.id}`, { method: "DELETE" });
      const remaining = topics.filter(item => item.id !== topic.id);
      setTopics(remaining);
      if (activeTopicId === topic.id) {
        const nextTopic = remaining.find(item => item.document_id === topic.document_id) ?? remaining[0] ?? null;
        setActiveTopicId(nextTopic?.id ?? null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to delete chat.");
    } finally {
      setDeletingTopicId(null);
    }
  }

  async function createTopic(filing: FilingWithTopics) {
    if (isBlocked(processing[filing.id]?.document.status ?? filing.status)) return;
    setCreatingDocumentId(filing.id);
    setError(null);
    try {
      const title = `${filenameStem(filing.original_filename)} · Analysis ${filing.topics.length + 1}`.slice(0, 120);
      const topic = await request<Topic>("/chat-topics", { method: "POST", body: JSON.stringify({ document_id: filing.id, title }) });
      upsertTopic({ ...topic, documents: { original_filename: filing.original_filename, status: filing.status } });
      setActiveTopicId(topic.id);
      setMessages([]);
      void loadTopics();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create a new chat for this filing.");
    } finally {
      setCreatingDocumentId(null);
    }
  }

  async function uploadFiling(file: File) {
    setUploadingFileName(file.name);
    setError(null);
    const data = new FormData();
    data.append("file", file);
    try {
      const result = await request<{ document: Filing; chat_topic?: Topic | null }>("/documents", { method: "POST", body: data });
      upsertDocument(result.document);
      setProcessing(current => ({ ...current, [result.document.id]: { document: result.document, job: { stage: result.document.status, status: result.document.status, progress: result.document.status === "queued" ? 10 : undefined } } }));
      if (result.chat_topic) {
        upsertTopic({ ...result.chat_topic, documents: { original_filename: result.document.original_filename, status: result.document.status } });
        setActiveTopicId(result.chat_topic.id);
      }
      void refreshDocumentStatus(result.document.id);
      void loadTopics();
      void loadDocuments();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to upload filing.");
    } finally {
      setUploadingFileName(null);
    }
  }

  async function retryProcessing(filing: Filing) {
    setRetryingDocumentId(filing.id);
    setError(null);
    try {
      const result = await request<{ document: Filing; job?: ProcessingJob | null }>(`/documents/${filing.id}/retry`, { method: "POST" });
      const queuedDocument = { ...filing, ...result.document, status: "queued", processing_error: null };
      upsertDocument(queuedDocument);
      setProcessing(current => ({ ...current, [filing.id]: { document: queuedDocument, job: result.job ?? { status: "queued", stage: "queued", progress: 5 } } }));
      void refreshDocumentStatus(filing.id);
      void loadDocuments();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to restart filing processing.");
    } finally {
      setRetryingDocumentId(null);
    }
  }

  async function openSourcePage(documentId: string, pageNumber: number) {
    if (!documentId || pageNumber < 1) return;
    setSourcePage({ pageNumber, loading: true });
    try {
      const page = await request<{ page_number: number; content: string }>(`/documents/${documentId}/pages/${pageNumber}`);
      setSourcePage({ pageNumber: page.page_number, content: page.content, loading: false });
    } catch (reason) {
      setSourcePage({
        pageNumber,
        loading: false,
        error: reason instanceof Error ? reason.message : "Unable to load the full source page.",
      });
    }
  }

  const activeFilename = activeFiling?.original_filename ?? "Choose a filing";
  const composerMessage = !activeTopic
    ? "Choose a filing topic first"
    : activeStatus === "failed"
      ? "This filing needs to be reprocessed before it can answer questions"
      : isActiveBlocked
        ? "This filing is still processing"
        : "Ask a question about this filing…";

  return (
    <main className="copilot-shell">
      <aside className="filing-sidebar" aria-label="Filing library">
        <div className="brand-lockup"><span className="brand-mark">A</span><span>Analyst Copilot</span></div>
        <p className="sidebar-description">Evidence-first analysis for filings you upload once and reuse.</p>
        <button className="add-filing-button" onClick={() => document.getElementById("filing")?.click()} disabled={Boolean(uploadingFileName)}>
          <span aria-hidden="true">+</span>{uploadingFileName ? "Uploading filing…" : "Add filing"}
        </button>
        <input
          id="filing"
          type="file"
          accept=".htm,.html,.pdf"
          hidden
          onChange={event => {
            const file = event.target.files?.[0];
            if (file) void uploadFiling(file);
            event.target.value = "";
          }}
        />
        {uploadingFileName && <div className="upload-indicator" role="status"><span className="upload-spinner" aria-hidden="true" />Sending {uploadingFileName}</div>}

        <div className="library-heading"><span>My filings</span><span className="filing-count">{filings.length}</span></div>
        <div className="filing-library">
          {documentsLoading && !filings.length && <p className="sidebar-state">Loading filing library…</p>}
          {documentsError && <p className="library-notice">Filing library is reconnecting. Your available chats are still usable.</p>}
          {!documentsLoading && !filings.length && <p className="sidebar-state">Add an HTML or Inline XBRL filing to begin.</p>}
          {filings.map(filing => {
            const filingStatus = processing[filing.id]?.document.status ?? filing.status;
            const filingIsActive = activeTopic?.document_id === filing.id;
            // Topics are returned newest-first. The filing-card action should
            // therefore reopen the analyst's most recent conversation, while
            // the individual topic rows remain available for explicit choice.
            const latestTopic = filing.topics[0];
            const openLatestTopic = () => {
              if (!latestTopic) return;
              setError(null);
              setActiveTopicId(latestTopic.id);
            };
            return (
              <section className={`filing-group ${filingIsActive ? "active" : ""}`} key={filing.id}>
                <button
                  className="filing-open-button"
                  aria-label={latestTopic ? `Open latest chat for ${filenameStem(filing.original_filename)}` : `No chats available for ${filenameStem(filing.original_filename)}`}
                  disabled={!latestTopic}
                  onClick={openLatestTopic}
                />
                <div className="filing-title-row">
                  <div title={filing.original_filename}>
                    <span className="filing-name">{filenameStem(filing.original_filename)}</span>
                    <span className="filing-filetype">{filing.original_filename.split(".").pop()?.toUpperCase() || "FILING"}</span>
                  </div>
                  <span className={`status-pill ${statusClass(filingStatus)}`}>{statusText(filingStatus)}</span>
                </div>
                <div className="topic-list">
                  {filing.topics.map(topic => (
                    <div className="topic-row" key={topic.id}>
                      <button className={`topic-button ${activeTopicId === topic.id ? "active" : ""}`} data-testid="topic" data-topic-id={topic.id} data-document-id={filing.id} onClick={() => { setError(null); setActiveTopicId(topic.id); }}>
                        <span className="topic-dot" aria-hidden="true" />
                        <span className="topic-title">{topic.title}</span>
                      </button>
                      <button
                        className="delete-topic-button"
                        data-testid="delete-topic"
                        aria-label={`Delete chat ${topic.title}`}
                        title="Delete chat"
                        disabled={deletingTopicId === topic.id}
                        onClick={() => void deleteTopic(topic)}
                      >
                        {deletingTopicId === topic.id ? "…" : "×"}
                      </button>
                    </div>
                  ))}
                </div>
                <button className="new-chat-button" onClick={() => void createTopic(filing)} disabled={creatingDocumentId === filing.id || isBlocked(filingStatus)}>
                  {creatingDocumentId === filing.id ? "Creating chat…" : "+ New chat"}
                </button>
              </section>
            );
          })}
        </div>
        <footer className="sidebar-footer">
          Submitted by Ankeet Hanny for AI Hackathon at VRIZE
        </footer>
      </aside>

      <section className="chat-workspace" aria-label="Filing chat">
        <header className="chat-header">
          <div className="active-filing-heading">
            <span>Active filing</span>
            <h1 title={activeFilename}>{activeFilename}</h1>
            {activeTopic && <p>{activeTopic.title}</p>}
          </div>
          <div className="chat-header-actions">
            {activeFiling && <span className={`status-pill ${statusClass(activeStatus)}`}>{statusText(activeStatus)}</span>}
            <button className="secondary-button" disabled={!activeTopic} onClick={renameTopic}>Rename chat</button>
          </div>
        </header>

        {error && <p className="error-banner" role="alert">{error}</p>}
        {activeFiling && isBlocked(activeStatus) && <ProcessingTracker filing={activeFiling} snapshot={activeSnapshot} onRetry={activeStatus === "failed" ? () => void retryProcessing(activeFiling) : undefined} retrying={retryingDocumentId === activeFiling.id} />}

        <div className="messages" ref={messagesRef} aria-live="polite">
          {messageLoading && <div className="message-loading"><span className="loading-orb" aria-hidden="true" />Loading this chat…</div>}
          {!messageLoading && messages.map(message => {
            const evidence = citedEvidence(message);
            return (
              <article className={`bubble ${message.role === "assistant" ? "assistant" : "user"}`} data-testid="message" data-message-id={message.id} data-role={message.role} key={message.id}>
                <div className="message-meta"><b>{message.role === "assistant" ? "Analyst Copilot" : "You"}</b>{message.answer_status && message.role === "assistant" && <span className={`answer-status ${message.answer_status}`} data-answer-status={message.answer_status}>{message.answer_status === "supported" ? "Evidence checked" : "Needs evidence"}</span>}</div>
                <p>{message.content}</p>
                {evidence.length > 0 && <button className="source-link" data-testid="source-link" onClick={() => { setSourcePage(null); setShowSources({ evidence, documentId: activeTopic?.document_id ?? "" }); }}>{evidenceLabel(evidence)}</button>}
                <time>{displayTime(message.created_at)}</time>
              </article>
            );
          })}
          {isThinking && <div className="thinking" data-testid="thinking" role="status"><span className="thinking-orbs" aria-hidden="true"><i className="thinking-orb orb-one" /><i className="thinking-orb orb-two" /><i className="thinking-orb orb-three" /></span><span>Understanding question <em>·</em> Searching filing <em>·</em> Reviewing evidence</span></div>}
          {activeTopic && !messageLoading && !messages.length && !isThinking && !isActiveBlocked && <div className="empty-chat"><span className="empty-icon">⌁</span><h2>Start an evidence-backed analysis</h2><p>Ask about financial performance, risks, ownership, or a filing table. Every supported answer includes its source.</p></div>}
          {!activeTopic && !messageLoading && <div className="empty-chat"><span className="empty-icon">⌁</span><h2>Select a filing topic</h2><p>Upload a filing once, then create as many focused chats as you need.</p></div>}
        </div>

        <form className="composer" onSubmit={ask}>
          <input disabled={!activeTopic || isThinking || isActiveBlocked} value={question} onChange={event => setQuestion(event.target.value)} placeholder={composerMessage} aria-label="Question about active filing" />
          <button disabled={!activeTopic || !question.trim() || isThinking || isActiveBlocked}>Send<span className="send-arrow" aria-hidden="true">↗</span></button>
        </form>

        {showSources && (
          <div className="modal" role="dialog" aria-modal="true" aria-label="Supporting evidence">
            <div className="evidence-panel">
              <button className="close-button" aria-label="Close evidence" onClick={() => { setSourcePage(null); setShowSources(null); }}>×</button>
              <p className="modal-kicker">Document-scoped sources</p>
              {sourcePage ? (
                <>
                  <h2>Page {sourcePage.pageNumber}</h2>
                  <p className="modal-description">Full extracted page text from the active filing.</p>
                  <button className="source-back-button" onClick={() => setSourcePage(null)}>← Back to supporting evidence</button>
                  {sourcePage.loading && <div className="message-loading"><span className="loading-orb" aria-hidden="true" />Loading source page…</div>}
                  {sourcePage.error && <p className="source-page-error" role="alert">{sourcePage.error}</p>}
                  {!sourcePage.loading && !sourcePage.error && <pre className="source-page-content">{sourcePage.content}</pre>}
                </>
              ) : (
                <>
                  <h2>Supporting evidence</h2>
                  <p className="modal-description">Sections are shown sequentially by page. Open any result to inspect its complete extracted page.</p>
                  <div className="evidence-list">
                    {showSources.evidence.map((item, index) => {
                      const location = evidenceLocation(item);
                      return (
                        <article className="evidence" data-testid="evidence-item" data-page-number={location.page ?? undefined} key={`${item.ordinal ?? index}-${item.excerpt.slice(0, 36)}`}>
                          <div><span className="evidence-number">{index + 1}</span><b>{location.page ? `Page ${location.page}` : "Filing evidence"} · {location.heading}</b></div>
                          <EvidenceExcerpt evidence={item} />
                          {location.page && <button className="open-page-button" data-testid="open-full-page" onClick={() => void openSourcePage(showSources.documentId, location.page!)}>Open full page</button>}
                        </article>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
