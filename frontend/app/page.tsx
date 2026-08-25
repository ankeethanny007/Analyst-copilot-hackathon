"use client";

import { FormEvent, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Evidence = { ordinal?: number; excerpt: string; page_number?: number; heading?: string; document_sections?: { page_number: number; heading: string | null; source_anchor?: string | null } };
type Message = { id: string; role: "user" | "assistant"; content: string; answer_status?: string | null; created_at: string; message_evidence?: Evidence[] };
type Topic = { id: string; title: string; document_id: string; documents?: { original_filename: string; status: string } };

function displayTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" }).format(new Date(value));
}

function evidenceLabel(evidence: Evidence[]) {
  const first = evidence[0]; const section = first.document_sections;
  const page = section?.page_number ?? first.page_number; const heading = section?.heading ?? first.heading ?? "Filing section";
  return `Page ${page} · ${heading}${evidence.length > 1 ? ` +${evidence.length - 1} more` : ""}`;
}

export default function Home() {
  const [topics, setTopics] = useState<Topic[]>([]); const [activeTopic, setActiveTopic] = useState<Topic | null>(null);
  const [messages, setMessages] = useState<Message[]>([]); const [question, setQuestion] = useState("");
  const [thinking, setThinking] = useState(false); const [showSources, setShowSources] = useState<Evidence[] | null>(null);
  const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(true);

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}/v1${path}`, { ...options, headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) } });
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? "Unable to reach Analyst Copilot.");
    return response.json();
  }

  async function loadTopics() {
    setLoading(true); setError(null);
    try { const data: Topic[] = await request("/chat-topics"); setTopics(data); setActiveTopic(current => current ?? data[0] ?? null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load chat topics."); }
    finally { setLoading(false); }
  }

  async function loadMessages(topic: Topic) {
    setError(null);
    try { setMessages(await request(`/chat-topics/${topic.id}/messages`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load messages."); }
  }

  useEffect(() => { void loadTopics(); }, []);
  useEffect(() => { if (activeTopic) void loadMessages(activeTopic); else setMessages([]); }, [activeTopic?.id]);

  async function ask(event: FormEvent) {
    event.preventDefault(); const prompt = question.trim(); if (!prompt || !activeTopic || thinking) return;
    setQuestion(""); setThinking(true); setError(null);
    try {
      const result = await request(`/chat-topics/${activeTopic.id}/messages`, { method: "POST", body: JSON.stringify({ content: prompt }) });
      const assistant: Message = { ...result.assistant_message, message_evidence: result.evidence.map((item: Evidence, index: number) => ({ ...item, ordinal: index + 1 })) };
      setMessages(current => [...current, result.user_message, assistant]); await loadTopics();
    } catch (reason) { setQuestion(prompt); setError(reason instanceof Error ? reason.message : "Unable to answer this question."); }
    finally { setThinking(false); }
  }

  async function renameTopic() {
    if (!activeTopic) return; const title = window.prompt("Rename chat", activeTopic.title)?.trim(); if (!title || title === activeTopic.title) return;
    try { const updated: Topic = await request(`/chat-topics/${activeTopic.id}`, { method: "PATCH", body: JSON.stringify({ title }) }); setTopics(items => items.map(item => item.id === updated.id ? { ...item, ...updated } : item)); setActiveTopic(item => item ? { ...item, ...updated } : item); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to rename chat."); }
  }

  const filename = activeTopic?.documents?.original_filename ?? "Select a filing";
  return <main><aside><h1>Analyst Copilot</h1><button disabled title="Available when Cloudflare R2 upload is connected">+ Add filing</button><h2>Chat topics</h2>{loading ? <p>Loading topics…</p> : topics.map(topic => <button className={`topic ${activeTopic?.id === topic.id ? "active" : ""}`} key={topic.id} onClick={() => setActiveTopic(topic)}>{topic.title}</button>)}{!loading && !topics.length && <p>No filing topics yet.</p>}</aside><section><header><div><small>Active filing</small><h2>{filename}</h2></div><button disabled={!activeTopic} onClick={renameTopic}>Rename chat</button></header>{error && <p className="error" role="alert">{error}</p>}<div className="messages">{messages.map(message => { const evidence = message.message_evidence ?? []; return <article className={`bubble ${message.role === "assistant" ? "assistant" : ""}`} key={message.id}><b>{message.role === "assistant" ? "Analyst Copilot" : "You"}</b><p>{message.content}</p>{evidence.length > 0 && <button className="source-link" onClick={() => setShowSources(evidence)}>{evidenceLabel(evidence)}</button>}<time>{displayTime(message.created_at)}</time></article>; })}{thinking && <div className="thinking">Understanding question · Searching filing · Reviewing evidence</div>}{activeTopic && !messages.length && !thinking && <p className="empty">Ask a question about this filing.</p>}</div><form onSubmit={ask}><input disabled={!activeTopic || thinking} value={question} onChange={e => setQuestion(e.target.value)} placeholder={activeTopic ? "Ask a question about this filing…" : "Select a filing topic first"} /><button disabled={!activeTopic || thinking}>Send</button></form>{showSources && <div className="modal" role="dialog" aria-modal="true"><div className="panel"><button className="close" aria-label="Close evidence" onClick={() => setShowSources(null)}>×</button><h2>Supporting evidence</h2>{showSources.map((item, index) => { const section = item.document_sections; return <article className="evidence" key={`${item.ordinal ?? index}-${item.excerpt.slice(0, 30)}`}><b>{index + 1}. Page {section?.page_number ?? item.page_number} · {section?.heading ?? item.heading ?? "Filing section"}</b><p>{item.excerpt}</p></article>; })}</div></div>}</section></main>;
}
