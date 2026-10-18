import React, { useEffect, useRef, useState } from "react";
import { ArtifactPanel } from "../widgets/ArtifactPanel";
import { ExternalLink } from "../widgets/env";
import { ProjectThumbnail } from "./ProjectThumbnail";
import { ProjectFile, ProjectMarkdown, ProjectMedia } from "./ProjectDocument";
import { request, SECTIONS, type Section, type WorkRecord, type WorkspaceData } from "./model";
import "./workspace.css";

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
  const [entries, setEntries] = useState<{ name: string; path: string; directory: boolean }[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  // One polling loop per open project. No writes, sync protocol or agent/model calls.
  useEffect(() => {
    let live = true; let running = false;
    activeBase.current = base;
    setData(null); setSelected(null); setFile(""); setFolder(""); setError(""); setSection("Work"); setQuery("");
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
    if (section !== "Files") return;
    let live = true;
    request<{ items: typeof entries }>(`${base}/files?path=${encodeURIComponent(folder)}`)
      .then(value => { if (live) setEntries(value.items); }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [base, section, folder, data?.file_revision]);
  const openRecord = async (id: string) => {
    setError(""); setFile("");
    try { const row = await request<WorkRecord>(`${base}/records/${encodeURIComponent(id)}`); if (activeBase.current === base) setSelected(row); }
    catch (err) { if (activeBase.current === base) setError(String(err)); }
  };
  const match = (text: string) => text.toLowerCase().includes(query.toLowerCase());
  const documents = (data?.documents || []).filter(row => match(`${row.title} ${row.path}`));
  const current = data?.documents.find(row => row.path === file);
  return <section className="pw-workspace" aria-label="Project workspace">
    <header className="pw-header"><button className="pw-text-link" type="button" onClick={onBack}>← All projects</button>
      <div className="pw-title"><div><h1>{data?.project.name || "Opening project…"}</h1>{data?.project.description ? <p>{data.project.description}</p> : null}</div><button type="button" className="es-btn es-btn-primary" onClick={onNewChat}>New project chat</button></div>
    </header>
    <nav className="pw-nav" aria-label="Project sections">{SECTIONS.map(name => <button type="button" key={name} aria-current={section === name ? "page" : undefined} onClick={() => { setSection(name); setSelected(null); setFile(""); setQuery(""); }}>{name}</button>)}</nav>
    {error ? <p className="pw-notice is-error" role="alert">{error}</p> : null}
    <div className="pw-content">{!data ? <p role="status">Loading project resources…</p> : file ? <ProjectFile key={`${base}:${file}`} base={base} path={file} onFile={setFile} onClose={() => setFile("")} revision={data.file_revision} technical={current?.markdown} />
      : selected ? <><button type="button" className="pw-text-link" onClick={() => setSelected(null)}>← Project work</button><Report key={`${selected.id}-${selected.revision}`} record={selected} base={base} onFile={setFile} onRecord={id => void openRecord(id)} onChat={onChat} onArtifact={onArtifact} /></>
      : section === "Work" ? <>
        <div className="pw-section-head"><input className="st-input" aria-label="Find project work" placeholder="Find project work…" value={query} onChange={event => setQuery(event.target.value)} /></div>
        {data.scan_error ? <p className="pw-notice" role="status">{data.scan_error}</p> : null}
        {data.scan_limited ? <p className="st-muted">Showing a bounded selection. Browse Files for other folders.</p> : null}
        <div className="pw-cards">{documents.map(row => <button type="button" key={row.path} className="pw-work-card" onClick={() => setFile(row.path)}>
          <ProjectThumbnail base={base} document={row} revision={data.file_revision} /><span className="pw-work-label"><strong>{row.title}</strong><small>{row.path}{row.markdown ? " · Technical notes" : ""}</small></span>
        </button>)}
        {data.artifacts.filter(row => match(row.title)).map(row => <button type="button" key={`artifact:${row.id}`} className="pw-work-card" onClick={() => onArtifact(row.id)}><span className="pw-thumbnail"><span className="pw-file-mark">{row.kind}</span></span><span className="pw-work-label"><strong>{row.title}</strong><small>Echo artifact · {row.versions} version(s)</small></span></button>)}
        </div>
        {data.media.filter(row => match(row.name)).length ? <section className="pw-creations"><div className="pw-section-head"><h2>Creations</h2><button className="pw-text-link" onClick={onCreations}>Open library</button></div><div className="pw-gallery">{data.media.filter(row => match(row.name)).map(row => <ProjectMedia key={row.id} base={base} path={row.name} caption={row.name} assetId={row.id} mediaKind={row.media_kind} />)}</div></section> : null}
        {!data.documents.length && !data.artifacts.length && !data.media.length ? <div className="pw-empty"><h3>Your project, in view</h3><p>HTML reports and Markdown in your attached folder appear here automatically. Keep visuals in artifacts/ and technical notes in docs/.</p><button className="pw-text-link" onClick={() => setSection("Files")}>Browse project files</button></div> : null}
        {query && !documents.length && !data.artifacts.some(row => match(row.title)) && !data.media.some(row => match(row.name)) ? <p>No work matches that search.</p> : null}
        {data.records.length ? <details className="pw-earlier"><summary>Earlier imported reports</summary><div className="pw-list">{data.records.map(row => <button type="button" key={row.id} onClick={() => void openRecord(row.id)}><strong>{row.title}</strong><small>Revision {row.revision}</small></button>)}</div></details> : null}
      </>
      : section === "Files" ? <><div className="pw-section-head"><span className="pw-path">{folder || "Project root"}</span><button className="es-btn es-btn-sm" disabled={!folder} onClick={() => setFolder(folder.split("/").slice(0, -1).join("/"))}>Up a folder</button></div><div className="pw-list">{entries.map(row => <button type="button" key={row.path} onClick={() => row.directory ? setFolder(row.path) : setFile(row.path)}><span>{row.directory ? "▸" : "·"} {row.name}</span><small>{row.directory ? "Folder" : row.path.split(".").pop()}</small></button>)}</div>{!entries.length ? <p>No previewable files in this folder.</p> : null}</>
      : section === "Conversations" ? <><div className="pw-list">{data.sessions.map(row => <button type="button" key={row.id} onClick={() => onChat(row.id)}><strong>{row.title || "Untitled conversation"}</strong><small>{new Date(row.at * 1000).toLocaleDateString()}</small></button>)}</div>{!data.sessions.length ? <p>No conversations yet. Start a project chat above.</p> : null}</>
      : section === "Research" ? <>{data.research.map((finding, i) => <article key={i} className="pw-evidence"><ProjectMarkdown text={finding.notes || "Saved sources"} base={base} path="" onFile={setFile} />{finding.sources?.map((source, n) => <div key={n}><ExternalLink href={source.url}>{source.title || source.url}</ExternalLink><p>{source.text || source.excerpt}</p></div>)}</article>)}<div className="pw-list">{data.sessions.map(row => <button type="button" key={row.id} onClick={() => onChat(row.id)}><span>Research in {row.title || "conversation"}</span><small>Chat notebook</small></button>)}</div>{!data.research.length && !data.sessions.length ? <p>No saved research yet.</p> : null}</>
      : section === "Routines" ? <><div className="pw-list">{data.routines.map(row => <button type="button" key={row.id} onClick={onRoutines}><strong>{row.name}</strong><small>{row.enabled ? "Enabled" : "Paused"} · {row.last_result_status || "No recorded result"}</small></button>)}</div>{!data.routines.length ? <p>No routines associated with this project.</p> : null}</>
      : <><div className="pw-list">{data.activity.map(row => <button type="button" key={row.id} onClick={() => onChat(row.session_id)}><span>{row.objective || row.id}</span><small>{row.status}</small></button>)}</div>{!data.activity.length ? <p>No recorded activity for this project.</p> : null}</>}
    </div>
  </section>;
}
