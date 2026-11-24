import React, { useEffect, useRef, useState } from "react";
import { ArtifactPanel } from "../widgets/ArtifactPanel";
import { ExternalLink } from "../widgets/env";
import { ProjectThumbnail } from "./ProjectThumbnail";
import { ProjectFile, ProjectMarkdown, ProjectMedia } from "./ProjectDocument";
import { request, SECTIONS, type Section, type ProjectDocument, type ProjectVault, type WorkRecord, type WorkspaceData, type WorkspaceDocuments } from "./model";
import "./workspace.css";

const DOCUMENT_FILTERS = ["All", "Workflows", "Markdown", "HTML"] as const;
type DocumentFilter = typeof DOCUMENT_FILTERS[number];

function isWorkflow(row: ProjectDocument): boolean {
  return row.kind === "workflow" || /(?:^|[\s/_.-])workflows?(?=$|[\s/_.-])/i.test(`${row.path} ${row.title}`);
}

const names: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex", echo: "Echo", other: "External agent" };

function Report({ record, base, onFile, onRecord, onChat, onArtifact }: {
  record: WorkRecord; base: string; onFile(path: string): void; onRecord(id: string): void; onChat(id: string): void; onArtifact(id: string): void;
}) {
  const [revision, setRevision] = useState(record.revision);
  const [doc, setDoc] = useState<{ kind: string; current: { content: string } } | null>(null);
  const [error, setError] = useState("");
  const choices = [record, ...(record.history || [])];
  const shown = choices.find(row => row.revision === revision) || record;
  useEffect(() => { setRevision(record.revision); }, [record.id, record.revision]);
  useEffect(() => {
    let live = true; setDoc(null); setError("");
    const apiBase = base.slice(0, base.indexOf("/projects/"));
    request<any>(`${apiBase}/lean/artifacts/${encodeURIComponent(shown.artifact_id)}?version=${shown.artifact_version}`)
      .then(value => { if (live) setDoc(value); }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [base, shown.artifact_id, shown.artifact_version]);
  return <article className="pw-report">
    <header className="pw-report-head"><span className="pw-eyebrow">{names[shown.agent] || shown.agent} · Agent-reported work</span>
      <h2>{shown.title}</h2><p>{shown.summary}</p>
      <p className="st-muted">Imported {new Date(shown.imported_at * 1000).toLocaleString()}{shown.source_commit ? ` · Reported commit ${shown.source_commit}` : ""}</p>
      <div className="pw-actions"><span className="pw-badge">{shown.status.replace(/_/g, " ")}</span>
        <label>Revision <select value={revision} onChange={event => setRevision(Number(event.target.value))}>{choices.map(row => <option key={row.revision} value={row.revision}>{row.revision}</option>)}</select></label>
        {shown.echo_session_id ? <button className="es-btn es-btn-sm" type="button" onClick={() => onChat(shown.echo_session_id)}>Originating chat</button> : <span className="st-muted">{shown.session_id ? `External session: ${shown.session_id}` : "External session not supplied"}</span>}
        {doc ? <button type="button" className="es-btn es-btn-sm" onClick={() => {
          const url = URL.createObjectURL(new Blob([doc.current.content], { type: "text/plain;charset=utf-8" }));
          const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${shown.id}-r${shown.revision}.${doc.kind === "html" ? "html" : "md"}`; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 2000);
        }}>Save report</button> : null}
      </div>
    </header>
    <div className="pw-report-body">{error ? <p role="alert">{error}</p> : !doc ? <p role="status">Opening report…</p>
      : doc.kind === "html" ? <ArtifactPanel apiBase={base.slice(0, base.indexOf("/projects/"))} artifactId={shown.artifact_id} version={shown.artifact_version} embedded onClose={() => undefined} />
      : <ProjectMarkdown base={base} text={doc.current.content} path={shown.report} onFile={onFile} />}
      {shown.media.length ? <div className="pw-gallery">{shown.media.map(media => <ProjectMedia key={media.path} base={base} path={media.path} caption={media.caption} />)}</div> : null}
      {shown.verification.length ? <section className="pw-evidence"><h3>Reported verification</h3><p className="st-muted">These results came from the originating agent. Importing checks references, not whether the work succeeds.</p>
        {shown.verification.map((check, i) => <div key={i}><strong>{check.label}</strong><span className={`pw-badge is-${check.result}`}>{check.result.replace(/_/g, " ")}</span>{check.evidence ? <pre>{check.evidence}</pre> : null}</div>)}
      </section> : <p className="st-muted">No verification evidence supplied.</p>}
      {shown.remaining.length ? <section><h3>Still to do</h3><ul>{shown.remaining.map((task, i) => <li key={i}>{task}</li>)}</ul></section> : null}
      {shown.files_changed.length ? <section><h3>Changed files</h3><div className="pw-links">{shown.files_changed.map(path => <button key={path} type="button" className="pw-text-link" onClick={() => onFile(path)}>{path}</button>)}</div><small className="st-muted">Deleted or moved files may no longer be available.</small></section> : null}
      {shown.artifact_ids.length ? <section><h3>Related artifacts</h3>{shown.artifact_ids.map(id => <button key={id} className="es-btn es-btn-sm" onClick={() => onArtifact(id)}>{id}</button>)}</section> : null}
      {shown.related_records.length ? <section><h3>Related work</h3>{shown.related_records.map(id => <button key={id} className="es-btn es-btn-sm" onClick={() => onRecord(id)}>{id}</button>)}</section> : null}
    </div>
  </article>;
}

export function ProjectWorkspace({ apiBase, projectId, onBack, onNewChat, onChat, onArtifact, onCreations, onRoutines }: {
  apiBase: string; projectId: string; onBack(): void; onNewChat(): void; onChat(id: string): void; onArtifact(id: string): void; onCreations(): void; onRoutines(): void;
}) {
  const base = `${apiBase}/projects/${encodeURIComponent(projectId)}/workspace`;
  const [loaded, setData] = useState<WorkspaceData | null>(null);
  const data = loaded?.project.id === projectId ? loaded : null;
  const activeBase = useRef(base);
  activeBase.current = base;
  const [section, setSection] = useState<Section>("Work");
  const [selected, setSelected] = useState<WorkRecord | null>(null);
  const [file, setFile] = useState("");
  const [folder, setFolder] = useState("");
  const [vault, setVault] = useState<ProjectVault | null>(null);
  const [vaultDocuments, setVaultDocuments] = useState<WorkspaceDocuments | null>(null);
  const [filter, setFilter] = useState<DocumentFilter>("All");
  const [showAllText, setShowAllText] = useState(false);
  const contentBase = vault?.id ? `${base}/vaults/${encodeURIComponent(vault.id)}` : base;
  const resources = vault?.id ? vaultDocuments : data;
  const [entries, setEntries] = useState<{ name: string; path: string; directory: boolean }[]>([]);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<"widgets" | "list">(() => {
    try { return localStorage.getItem("echospeak.projectWork.view") === "list" ? "list" : "widgets"; } catch { return "widgets"; }
  });
  const changeView = (next: "widgets" | "list") => {
    setView(next);
    try { localStorage.setItem("echospeak.projectWork.view", next); } catch { /* Session-only preference when storage is unavailable. */ }
  };
  const [error, setError] = useState("");
  // One polling loop per open project. No writes, sync protocol or agent/model calls.
  useEffect(() => {
    let live = true; let running = false;
    activeBase.current = base;
    setData(null); setSelected(null); setFile(""); setFolder(""); setError(""); setSection("Work"); setQuery(""); setFilter("All"); setVault(null); setVaultDocuments(null);
    const refresh = async () => {
      if (running || document.visibilityState !== "visible") return;
      running = true;
      try {
        const result = await request<WorkspaceData>(base);
        if (live) {
          setData(previous => JSON.stringify(previous) === JSON.stringify(result) ? previous : result);
          setError("");
        }
      } catch (err) { if (live) setError(String(err)); }
      finally { running = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { live = false; activeBase.current = ""; window.clearInterval(timer); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [base]);
  useEffect(() => {
    if (contentBase === base) return;
    let live = true;
    let pending = false;
    const refresh = async () => {
      if (pending || document.visibilityState !== "visible") return;
      pending = true;
      try {
        const result = await request<WorkspaceDocuments>(contentBase);
        if (live) setVaultDocuments(result);
      } catch (err) {
        if (live) { setVaultDocuments(null); setError(String(err)); }
      } finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    document.addEventListener("visibilitychange", refresh);
    return () => { live = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [base, contentBase]);
  useEffect(() => {
    if (section !== "Files") return;
    let live = true;
    setEntries([]);
    request<{ items: typeof entries }>(`${contentBase}/files?path=${encodeURIComponent(folder)}`)
      .then(value => { if (live) setEntries(value.items); }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [contentBase, section, folder, resources?.file_revision]);
  const browseVault = (next: ProjectVault | null) => {
    setVault(next?.id ? next : null);
    setVaultDocuments(null); setShowAllText(false);
    setFile(""); setQuery(""); setFilter("All"); setError("");
    setFolder(next && !next.id ? next.path : "");
    setSection(next && !next.id ? "Files" : "Work");
  };
  const openRecord = async (id: string) => {
    setError(""); setFile("");
    try { const row = await request<WorkRecord>(`${base}/records/${encodeURIComponent(id)}`); if (activeBase.current === base) setSelected(row); }
    catch (err) { if (activeBase.current === base) setError(String(err)); }
  };
  const match = (text: string) => text.toLowerCase().includes(query.toLowerCase());
  const documents = (resources?.documents || []).filter(row => match(`${row.title} ${row.path}`) &&
    (filter === "All" || (filter === "Workflows" && isWorkflow(row)) ||
      (filter === "Markdown" && row.kind === "markdown") || (filter === "HTML" && row.kind === "html")));
  const textDocuments = documents.filter(row => row.kind !== "html")
    .sort((a, b) => Number(isWorkflow(b)) - Number(isWorkflow(a)));
  const visibleText = filter === "All" && !query && !showAllText ? textDocuments.slice(0, 5) : textDocuments;
  const reports = documents.filter(row => row.kind === "html");
  const htmlArtifacts = !vault && (filter === "All" || filter === "HTML")
    ? (data?.artifacts || []).filter(row => row.kind === "html" && match(row.title)) : [];
  const current = resources?.documents.find(row => row.path === file);
  return <section className="pw-workspace" aria-label="Project workspace">
    <header className="pw-header"><button className="pw-text-link pw-back" type="button" onClick={onBack} aria-label="All projects" title="All projects">←</button>
      <div className="pw-title"><h1 title={data?.project.description}>{data?.project.name || "Opening project…"}</h1><button type="button" className="es-btn es-btn-primary" onClick={onNewChat}>New chat</button></div>
    </header>
    <nav className="pw-nav" aria-label="Project sections">{SECTIONS.map(name => <button type="button" key={name} aria-current={section === name ? "page" : undefined} onClick={() => { setSection(name); setSelected(null); setFile(""); setQuery(""); if (name !== "Work" && name !== "Files") { setVault(null); setVaultDocuments(null); setFolder(""); } }}>{name}</button>)}</nav>
    {error ? <p className="pw-notice is-error" role="alert">{error}</p> : null}
    <div className="pw-content">
    {vault ? <div className="pw-vault-location"><button type="button" className="pw-text-link" onClick={() => browseVault(null)}>← Project documents</button><strong>{vault.name}</strong></div> : null}
    {!data ? <p role="status">Loading project resources…</p> : file ? <ProjectFile key={`${contentBase}:${file}`} base={contentBase} path={file} onFile={setFile} onClose={() => setFile("")} revision={current?.revision || resources?.file_revision} technical={current?.markdown} />
      : selected ? <><button type="button" className="pw-text-link" onClick={() => setSelected(null)}>← Project work</button><Report key={`${selected.id}-${selected.revision}`} record={selected} base={base} onFile={setFile} onRecord={id => void openRecord(id)} onChat={onChat} onArtifact={onArtifact} /></>
      : section === "Work" ? <>
        {!vault && Boolean(data.vaults?.length) ? <section className="pw-vaults" aria-label="Obsidian vaults">
          <h2>Obsidian vaults</h2>
          <div className="pw-vault-list">{data.vaults?.map(item => <button key={item.id || item.path} type="button" onClick={() => browseVault(item)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden><path d="m8 4 9-2 3 6-3 12-8 2-5-6 1-7 3-5Z"/><path d="m5 9 7 5 8-6m-8 6-3 8m3-8 5 6"/></svg>
            <span><strong>{item.name}</strong><small title={item.path}>{item.id ? "Connected vault" : item.path || "Project folder"}</small></span>
            <span aria-hidden>›</span></button>)}</div>
        </section> : null}
        <div className="pw-section-head pw-work-toolbar"><input className="st-input" aria-label="Find project documents" placeholder="Find documents and workflows…" value={query} onChange={event => setQuery(event.target.value)} />
          <div className="pw-view-switch" role="group" aria-label="Project work view">{(["widgets", "list"] as const).map(mode => <button key={mode} type="button" aria-pressed={view === mode} onClick={() => changeView(mode)} title={mode === "widgets" ? "Widget view" : "List view"}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">{mode === "widgets" ? <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></> : <><path d="M8 5h13M8 12h13M8 19h13M3 5h1M3 12h1M3 19h1"/></>}</svg>{mode === "widgets" ? "Widgets" : "List"}
          </button>)}</div></div>
        <div className="pw-document-filters" role="group" aria-label="Document type">
          {DOCUMENT_FILTERS.map(name => <button key={name} type="button" aria-pressed={filter === name} onClick={() => setFilter(name)}>{name}</button>)}
        </div>
        {resources?.scan_error ? <p className="pw-notice" role="status">{resources.scan_error}</p> : null}
        {resources?.scan_limited ? <p className="st-muted">Showing a bounded selection. Browse Files for other folders.</p> : null}
        {textDocuments.length ? <div className="pw-list pw-document-list">{visibleText.map(row => <button type="button" key={row.path} onClick={() => setFile(row.path)}>
          <span><strong>{row.title}</strong><small>{row.path}</small></span><span className="pw-type">{isWorkflow(row) ? "Workflow" : "Markdown"}</span>
        </button>)}{visibleText.length < textDocuments.length ? <button type="button" onClick={() => setShowAllText(true)}>Show all {textDocuments.length} text documents</button> : null}</div> : null}
        {reports.length || htmlArtifacts.length ? <div className={`pw-cards${view === "list" ? " is-list" : ""}`}>{reports.map(row => <button type="button" key={row.path} className="pw-work-card" onClick={() => setFile(row.path)}>
          <ProjectThumbnail base={contentBase} document={row} revision={row.revision || `${row.modified_at}:${row.size}`} /><span className="pw-work-label"><strong>{row.title}</strong><small>{row.path}{row.markdown ? " · Technical notes" : ""}</small></span>
        </button>)}
        {htmlArtifacts.map(row => <button type="button" key={`artifact:${row.id}`} className="pw-work-card" onClick={() => onArtifact(row.id)}><span className="pw-thumbnail"><span className="pw-file-mark">{row.kind}</span></span><span className="pw-work-label"><strong>{row.title}</strong><small>Echo artifact · {row.versions} version(s)</small></span></button>)}
        </div> : null}
        {!resources && !error ? <p role="status">Opening vault…</p> : null}
        {resources && !documents.length && !htmlArtifacts.length ? <div className="pw-empty"><h3>{query || filter !== "All" ? "No matching documents" : "No documents found"}</h3><button className="pw-text-link" onClick={() => setSection("Files")}>Browse files</button></div> : null}

      </>
      : section === "Files" ? <><div className="pw-section-head"><span className="pw-path">{folder || (vault ? vault.name : "Project root")}</span><button className="es-btn es-btn-sm" disabled={!folder} onClick={() => setFolder(folder.split("/").slice(0, -1).join("/"))}>Up a folder</button></div><div className="pw-list">{entries.map(row => <button type="button" key={row.path} onClick={() => row.directory ? setFolder(row.path) : setFile(row.path)}><span>{row.directory ? "▸" : "·"} {row.name}</span><small>{row.directory ? "Folder" : row.path.split(".").pop()}</small></button>)}</div>{!entries.length ? <p>No previewable files in this folder.</p> : null}        {!vault && data.records.length ? <details className="pw-earlier"><summary>Earlier imported reports</summary><div className="pw-list">{data.records.map(row => <button type="button" key={row.id} onClick={() => void openRecord(row.id)}><strong>{row.title}</strong><small>Revision {row.revision}</small></button>)}</div></details> : null}</>
      : section === "Conversations" ? <><div className="pw-list">{data.sessions.map(row => <button type="button" key={row.id} onClick={() => onChat(row.id)}><strong>{row.title || "Untitled conversation"}</strong><small>{new Date(row.at * 1000).toLocaleDateString()}</small></button>)}</div>{!data.sessions.length ? <p>No conversations yet. Start a project chat above.</p> : null}</>
      : section === "Research" ? <>{data.research.map((finding, i) => <article key={i} className="pw-evidence"><ProjectMarkdown text={finding.notes || "Saved sources"} base={base} path="" onFile={setFile} />{finding.sources?.map((source, n) => <div key={n}><ExternalLink href={source.url}>{source.title || source.url}</ExternalLink><p>{source.text || source.excerpt}</p></div>)}</article>)}<div className="pw-list">{data.sessions.map(row => <button type="button" key={row.id} onClick={() => onChat(row.id)}><span>Research in {row.title || "conversation"}</span><small>Chat notebook</small></button>)}</div>{!data.research.length && !data.sessions.length ? <p>No saved research yet.</p> : null}</>
      : section === "Routines" ? <><div className="pw-list">{data.routines.map(row => <button type="button" key={row.id} onClick={onRoutines}><strong>{row.name}</strong><small>{row.enabled ? "Enabled" : "Paused"} · {row.last_result_status || "No recorded result"}</small></button>)}</div>{!data.routines.length ? <p>No routines associated with this project.</p> : null}</>
      : null}
    </div>
  </section>;
}
