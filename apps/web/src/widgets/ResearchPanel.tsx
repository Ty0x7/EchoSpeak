import React, { useEffect, useState } from "react";
import { openExternal } from "./env";
import "./researchPanel.css";

type Source = { id: string; url: string; title: string; excerpt: string; inspected: boolean; updated: number; expires_at: number };
type Notebook = { session_id: string; sources: Source[]; notes: string; retention_days: number };
type Passage = { text: string; next_offset: number | null; total_chars: number };

export function ResearchPanel({ apiBase, sessionId, active = true }: { apiBase: string; sessionId: string; active?: boolean }) {
  const [book, setBook] = useState<Notebook | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [passage, setPassage] = useState<Passage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    if (!sessionId || !active) return;
    const controller = new AbortController();
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const response = await fetch(`${apiBase}/sessions/${encodeURIComponent(sessionId)}/research?query=${encodeURIComponent(query)}`, { signal: controller.signal });
        if (!response.ok) throw new Error("Could not load this chat’s research notebook.");
        const data: Notebook = await response.json();
        if (!controller.signal.aborted) { setBook(data); setError(""); }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Notebook unavailable.");
      } finally { busy = false; }
    };
    const delay = window.setTimeout(() => void load(), 200);
    const interval = window.setInterval(() => void load(), 4000);
    return () => { controller.abort(); window.clearTimeout(delay); window.clearInterval(interval); };
  }, [apiBase, sessionId, query, active]);
  useEffect(() => {
    setPassage(null);
    if (!selected) return;
    const controller = new AbortController();
    setLoading(true);
    void fetch(`${apiBase}/sessions/${encodeURIComponent(sessionId)}/research/sources/${encodeURIComponent(selected)}?offset=${offset}`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("This source expired or is no longer available.");
        const data: Passage = await response.json();
        if (!controller.signal.aborted) { setPassage(data); setError(""); }
      }).catch(err => { if (!controller.signal.aborted) setError(err.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [apiBase, sessionId, selected, offset]);
  const sources = book?.session_id === sessionId ? book.sources : [];
  return <section className="notebook-panel" aria-label="This chat’s research notebook">
    <p className="notebook-hint">Sources and working notes for this chat. Kept for seven days; separate from personal memory.</p>
    <input className="notebook-search" aria-label="Search notebook sources" placeholder="Search sources…" value={query} onChange={e => setQuery(e.target.value)} />
    {error && <p role="status" className="notebook-error">{error}</p>}
    {book?.session_id === sessionId && book.notes && <details className="notebook-notes" open><summary>Working notes</summary><p>{book.notes}</p></details>}
    {!sources.length && <p className="ap-empty">{book ? "No matching sources. Ask Echo to research a question to collect evidence here." : "Loading sources…"}</p>}
    <ol className="notebook-sources">{sources.map(source => <li key={source.id}>
      <button className="notebook-source" type="button" onClick={() => { setSelected(source.id === selected ? "" : source.id); setOffset(0); }} aria-expanded={selected === source.id}>
        <strong>{source.title || source.url}</strong>
        <span>{source.inspected ? "Page read" : "Search result · page not read"}</span>
        <p>{source.excerpt}</p>
        <small>Expires {new Date(source.expires_at * 1000).toLocaleString()}</small>
      </button>
      {/^https?:\/\//i.test(source.url) && <button type="button" className="es-btn es-btn-sm" onClick={() => void openExternal(source.url)}>Open original</button>}
      {selected === source.id && <div className="notebook-passage">
        {loading ? <p>Loading passage…</p> : passage && <>
          <pre>{passage.text}</pre>
          <div className="notebook-pages">
            <button type="button" className="es-btn es-btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 12000))}>Previous passage</button>
            <button type="button" className="es-btn es-btn-sm" disabled={passage.next_offset == null} onClick={() => setOffset(passage.next_offset ?? 0)}>Next passage</button>
          </div>
        </>}
      </div>}
    </li>)}</ol>
  </section>;
}
