"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

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
  const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(true); const [uploading, setUploading] = useState(false);
  const [token, setToken] = useState<string | null>(null); const [sessionReady, setSessionReady] = useState(false); const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [signingIn, setSigningIn] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}/v1${path}`, { ...options, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options?.headers ?? {}) } });
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

  useEffect(() => { setToken(window.localStorage.getItem("analyst-copilot-access-token")); setSessionReady(true); }, []);
  useEffect(() => { if (sessionReady && token) void loadTopics(); else if (sessionReady) { setTopics([]); setActiveTopic(null); setMessages([]); setLoading(false); } }, [sessionReady, token]);
  useEffect(() => { if (activeTopic) void loadMessages(activeTopic); else setMessages([]); }, [activeTopic?.id]);
  useEffect(() => { messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight }); }, [messages, thinking]);

  async function ask(event: FormEvent) {
    event.preventDefault(); const prompt = question.trim(); if (!prompt || !activeTopic || thinking) return;
    const pendingId = `pending-${Date.now()}`;
    setQuestion(""); setError(null); setMessages(current => [...current, { id: pendingId, role: "user", content: prompt, created_at: new Date().toISOString() }]);
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve())); setThinking(true);
    try {
      const result = await request(`/chat-topics/${activeTopic.id}/messages`, { method: "POST", body: JSON.stringify({ content: prompt }) });
      const assistant: Message = { ...result.assistant_message, message_evidence: result.evidence.map((item: Evidence, index: number) => ({ ...item, ordinal: index + 1 })) };
      setMessages(current => [...current.filter(message => message.id !== pendingId), result.user_message, assistant]); await loadTopics();
    } catch (reason) { setMessages(current => current.filter(message => message.id !== pendingId)); setQuestion(prompt); setError(reason instanceof Error ? reason.message : "Unable to answer this question."); }
    finally { setThinking(false); }
  }

  async function renameTopic() {
    if (!activeTopic) return; const title = window.prompt("Rename chat", activeTopic.title)?.trim(); if (!title || title === activeTopic.title) return;
    try { const updated: Topic = await request(`/chat-topics/${activeTopic.id}`, { method: "PATCH", body: JSON.stringify({ title }) }); setTopics(items => items.map(item => item.id === updated.id ? { ...item, ...updated } : item)); setActiveTopic(item => item ? { ...item, ...updated } : item); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to rename chat."); }
  }

  async function uploadFiling(file: File) {
    setUploading(true); setError(null); const data = new FormData(); data.append("file", file);
    try { const response = await fetch(`${API}/v1/documents`, { method: "POST", body: data, headers: token ? { Authorization: `Bearer ${token}` } : {} }); if (!response.ok) throw new Error((await response.json()).detail); const result = await response.json(); await loadTopics(); if (result.chat_topic) setActiveTopic(result.chat_topic); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to upload filing."); }
    finally { setUploading(false); }
  }

  async function signIn(event: FormEvent) {
    event.preventDefault(); setSigningIn(true); setError(null); try { const response = await fetch("/api/auth/sign-in", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) }); const session = await response.json(); if (!response.ok) throw new Error(session.error_description ?? "Sign-in failed."); window.localStorage.setItem("analyst-copilot-access-token", session.access_token); setToken(session.access_token); setPassword(""); } catch (reason) { setError(reason instanceof Error ? reason.message : "Sign-in failed."); } finally { setSigningIn(false); }
  }

  if (!sessionReady) return <main className="auth-page"><section className="auth-panel">Loading secure session…</section></main>;
  if (!token) return <main className="auth-page"><section className="auth-hero"><div className="brand"><span className="brand-mark">A</span><span>Analyst Copilot</span></div><div className="auth-copy"><p className="eyebrow">Evidence-first research</p><h1>Every answer has a trail.</h1><p>Search filings, inspect precise evidence, and keep every analysis tied to its source.</p></div><div className="signal-grid"><div><b>01</b><span>Private filing workspaces</span></div><div><b>02</b><span>Section-level evidence</span></div><div><b>03</b><span>Answers that know when to abstain</span></div></div><p className="hero-foot">ANALYST WORKBENCH <i /> SECURE BY DESIGN</p></section><section className="auth-panel"><div className="panel-brand"><span className="brand-mark">A</span><span>Analyst Copilot</span></div><div className="auth-heading"><p className="eyebrow">Welcome back</p><h1>Sign in to your workspace</h1><p>Your filings and chats remain private to your account.</p></div>{error && <p className="error" role="alert">{error}</p>}<form className="auth-form" onSubmit={signIn}><label>Email<input required type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" placeholder="you@company.com"/></label><label>Password<input required type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" placeholder="••••••••"/></label><button disabled={signingIn}>{signingIn ? "Signing in…" : "Enter workspace →"}</button></form><p className="auth-foot">Protected document intelligence</p></section></main>;
  const filename = activeTopic?.documents?.original_filename ?? "Select a filing";
  return <main><aside><h1>Analyst Copilot</h1><button onClick={() => { window.localStorage.removeItem("analyst-copilot-access-token"); setToken(null); }}>Sign out</button><button onClick={() => document.getElementById("filing")?.click()} disabled={uploading}>{uploading ? "Uploading…" : "+ Add filing"}</button><input id="filing" type="file" accept=".htm,.html,.pdf" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void uploadFiling(file); event.target.value = ""; }} /><h2>Chat topics</h2>{loading ? <p>Loading topics…</p> : topics.map(topic => <button className={`topic ${activeTopic?.id === topic.id ? "active" : ""}`} key={topic.id} onClick={() => setActiveTopic(topic)}>{topic.title}</button>)}{!loading && !topics.length && <p>No filing topics yet.</p>}</aside><section><header><div><small>Active filing</small><h2>{filename}</h2></div><button disabled={!activeTopic} onClick={renameTopic}>Rename chat</button></header>{error && <p className="error" role="alert">{error}</p>}<div className="messages" ref={messagesRef}>{messages.map(message => { const evidence = message.message_evidence ?? []; return <article className={`bubble ${message.role === "assistant" ? "assistant" : ""}`} key={message.id}><b>{message.role === "assistant" ? "Analyst Copilot" : "You"}</b><p>{message.content}</p>{evidence.length > 0 && <button className="source-link" onClick={() => setShowSources(evidence)}>{evidenceLabel(evidence)}</button>}<time>{displayTime(message.created_at)}</time></article>; })}{thinking && <div className="thinking">Understanding question · Searching filing · Reviewing evidence</div>}{activeTopic && !messages.length && !thinking && <p className="empty">Ask a question about this filing.</p>}</div><form onSubmit={ask}><input disabled={!activeTopic || thinking} value={question} onChange={e => setQuestion(e.target.value)} placeholder={activeTopic ? "Ask a question about this filing…" : "Select a filing topic first"} /><button disabled={!activeTopic || thinking}>Send</button></form>{showSources && <div className="modal" role="dialog" aria-modal="true"><div className="panel"><button className="close" aria-label="Close evidence" onClick={() => setShowSources(null)}>×</button><h2>Supporting evidence</h2>{showSources.map((item, index) => { const section = item.document_sections; return <article className="evidence" key={`${item.ordinal ?? index}-${item.excerpt.slice(0, 30)}`}><b>{index + 1}. Page {section?.page_number ?? item.page_number} · {section?.heading ?? item.heading ?? "Filing section"}</b><p>{item.excerpt}</p></article>; })}</div></div>}</section></main>;
}
