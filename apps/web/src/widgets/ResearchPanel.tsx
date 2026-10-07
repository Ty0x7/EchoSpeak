import React, { useEffect, useRef, useState } from "react";
import { openExternal } from "./env";
import "./researchPanel.css";
import { creationRequest } from "../creations/api";

type Source = { id: string; url: string; title: string; excerpt: string; inspected: boolean; updated: number; expires_at: number };
type Notebook = { session_id: string; sources: Source[]; notes: string; retention_days: number };
type Passage = { text: string; next_offset: number | null; total_chars: number };

/** Small line icons for the panel's quiet text actions. */
const LINK_ICON = { width: 12, height: 12, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

export function ResearchPanel({ apiBase, sessionId, active = true }: { apiBase: string; sessionId: string; active?: boolean }) {
  const [book, setBook] = useState<Notebook | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [passage, setPassage] = useState<Passage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [project, setProject] = useState<{ id: string; name: string; metadata?: { brief?: string } } | null>(null);
  const [brief, setBrief] = useState("");
  const projectId = useRef("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const select = () => { const id = sessionStorage.getItem(`echospeak:research-source:${sessionId}`); if (id) { setSelected(id); setOffset(0); } };
    const receive = (event: Event) => { if ((event as CustomEvent).detail?.session === sessionId) { setQuery(""); select(); } };
    select(); window.addEventListener("echospeak:research-source", receive);
    return () => window.removeEventListener("echospeak:research-source", receive);
  }, [sessionId]);
  useEffect(() => {
    if (!active) return;
    let disposed = false;
    void creationRequest(apiBase, `/threads/${encodeURIComponent(sessionId)}/state`).then(async state => {
      const p = state.active_project_id ? await creationRequest(apiBase, `/projects/${encodeURIComponent(state.active_project_id)}`) : null;
      if (!disposed) { setProject(p); if (projectId.current !== (p?.id || "")) { projectId.current = p?.id || ""; setBrief(p?.metadata?.brief || ""); } }
    }).catch(() => {});
    return () => { disposed = true; };
  }, [apiBase, sessionId, active]);
  const action = async (work: () => Promise<void>) => { setBusy(true); setNotice(""); try { await work(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); } };
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
    <div className="notebook-actions"><button type="button" className="notebook-link" onClick={() => { setDraft(book?.notes || "## Findings\n\n## Open questions\n\n## Conflicting findings\n"); setEditing(true); }}><svg {...LINK_ICON}><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>Edit notes</button><button type="button" className="notebook-link" disabled={busy} onClick={() => void action(async () => {
      const report = await creationRequest(apiBase, `/sessions/${encodeURIComponent(sessionId)}/research/report`);
      const url = URL.createObjectURL(new Blob([report.text], { type: "text/markdown;charset=utf-8" }));
      const a = document.createElement("a"); a.href = url; a.download = report.filename; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    })}><svg {...LINK_ICON}><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></svg>Export report</button></div>
    {editing && <div className="notebook-editor"><label>Findings, open questions and conflicts<textarea aria-label="Working research notes" value={draft} maxLength={8000} onChange={e => setDraft(e.target.value)} /></label><div className="notebook-pages"><button className="es-btn es-btn-sm" disabled={busy} onClick={() => void action(async () => { const data = await creationRequest(apiBase, `/sessions/${encodeURIComponent(sessionId)}/research/notes`, "PUT", { text: draft }); setBook(current => current ? { ...current, notes: data.notes } : current); setEditing(false); })}>Save notes</button><button className="es-btn es-btn-sm" onClick={() => setEditing(false)}>Cancel</button></div></div>}
    {project && <details className="notebook-notes"><summary>Project · {project.name}</summary><div className="notebook-editor"><label>Project brief<textarea aria-label="Project brief" value={brief} maxLength={4000} onChange={e => setBrief(e.target.value)} /></label><div className="notebook-pages"><button className="es-btn es-btn-sm" disabled={busy} onClick={() => void action(async () => { await creationRequest(apiBase, `/sessions/${encodeURIComponent(sessionId)}/project-brief`, "PUT", { text: brief }); setNotice("Project brief saved for future chats."); })}>Save brief</button><button className="es-btn es-btn-sm" disabled={busy || editing} onClick={() => void action(async () => { const data = await creationRequest(apiBase, `/sessions/${encodeURIComponent(sessionId)}/research/save-to-project`, "POST", { project_id: project.id, source_ids: sources.filter(s => s.inspected).slice(0, 20).map(s => s.id), notes: book?.notes || "" }); setNotice(`Saved notes and ${data.source_count} read sources to ${project.name}.`); })}>Save findings to project</button></div><small>Saves working notes and up to 20 read sources from the current filter. This is separate from personal memory.</small></div></details>}
    {notice && <p role="status">{notice}</p>}
    {book?.session_id === sessionId && book.notes && <details className="notebook-notes" open><summary>Working notes</summary><p>{book.notes}</p></details>}
    {!sources.length && <p className="ap-empty">{book ? "No matching sources. Ask Echo to research a question to collect evidence here." : "Loading sources…"}</p>}
    <ol className="notebook-sources">{sources.map(source => <li key={source.id}>
      <button className="notebook-source" type="button" onClick={() => { setSelected(source.id === selected ? "" : source.id); setOffset(0); }} aria-expanded={selected === source.id}>
        <strong>{source.title || source.url}</strong>
        <span>{source.inspected ? "Page read" : "Search result · page not read"}</span>
        <p>{source.excerpt}</p>
        <small>Expires {new Date(source.expires_at * 1000).toLocaleString()}</small>
      </button>
      {/^https?:\/\//i.test(source.url) && <button type="button" className="notebook-link" onClick={() => void openExternal(source.url)}><svg {...LINK_ICON}><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>Open original</button>}
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
