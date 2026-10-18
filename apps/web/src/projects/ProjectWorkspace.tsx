import React, { useCallback, useEffect, useRef, useState } from "react";
import { ArtifactPanel } from "../widgets/ArtifactPanel";
import { ExternalLink } from "../widgets/env";
import { ShowMore, useShowMore } from "../lean/ShowMore";
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
  const [data, setData] = useState<WorkspaceData | null>(null);
  const [section, setSection] = useState<Section>("Reports");
  const [selected, setSelected] = useState<WorkRecord | null>(null);
  const [file, setFile] = useState("");
  const [folder, setFolder] = useState("");
  const [entries, setEntries] = useState<{ name: string; path: string; directory: boolean }[]>([]);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const filteredReports = (data?.records || []).filter(row => `${row.title} ${row.summary} ${row.agent}`.toLowerCase().includes(query.toLowerCase()));
  const more = useShowMore(filteredReports);
  const importer = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const reload = useCallback(async () => { const result = await request<WorkspaceData>(base); if (alive.current) setData(result); return result; }, [base]);
  useEffect(() => {
    alive.current = true; let active = true; setData(null); setSelected(null); setFile(""); setFolder(""); setError("");
    const open = async () => {
      try {
        const result = await request<WorkspaceData>(base);
        if (!active) return;
        setData(result);
        if (result.project.sharing_enabled) {
          const sync = await request<{ errors: { error: string }[] }>(`${base}/sync`, {});
          if (!active) return;
          if (sync.errors.length) setMessage(`${sync.errors.length} report(s) need attention. Sync reports to inspect errors.`);
          const updated = await request<WorkspaceData>(base);
          if (active) setData(updated);
        }
      } catch (err) { if (active) setError(String(err)); }
    };
    void open();
    return () => { active = false; alive.current = false; };
  }, [base]);
  useEffect(() => {
    if (!data?.project.sharing_enabled) return;
    let running = false;
    const refresh = async () => {
      if (document.visibilityState !== "visible" || running) return;
      running = true;
      try { await request(`${base}/sync`, {}); await reload(); } catch { /* explicit refresh surfaces errors; keep the last good view */ }
      finally { running = false; }
    };
    const timer = window.setInterval(() => void refresh(), 30000);
    window.addEventListener("focus", refresh);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [base, data?.project.sharing_enabled, reload]);
  useEffect(() => {
    if (section !== "Files") return;
    let live = true; setEntries([]);
    request<{ items: typeof entries }>(`${base}/files?path=${encodeURIComponent(folder)}`).then(value => { if (live) setEntries(value.items); }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [base, section, folder]);
  const act = async (task: () => Promise<string>) => {
    setBusy(true); setError(""); setMessage("");
    try { const text = await task(); if (alive.current) { setMessage(text); await reload(); } }
    catch (err) { if (alive.current) setError(String(err)); }
    finally { if (alive.current) setBusy(false); }
  };
  const openRecord = async (id: string) => {
    setError(""); setFile("");
    try { const row = await request<WorkRecord>(`${base}/records/${encodeURIComponent(id)}`); if (alive.current) setSelected(row); }
    catch (err) { if (alive.current) setError(String(err)); }
  };
  const importFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const input = event.target; const value = input.files?.[0]; input.value = "";
    if (!value) return;
    await act(async () => { if (value.size > 64000) throw new Error("Manifest must be under 64 KB"); await request(`${base}/imports`, JSON.parse(await value.text())); return "Report imported. Results remain agent-reported."; });
  };
  return <section className="pw-workspace" aria-label="Project workspace">
    <header className="pw-header"><button className="pw-text-link" type="button" onClick={onBack}>← All projects</button>
      <div className="pw-title"><div><span className="pw-eyebrow">Project workspace</span><h1>{data?.project.name || "Opening project…"}</h1><p>{data?.project.description || "Your work, evidence and conversations, connected."}</p></div><button type="button" className="es-btn es-btn-primary" onClick={onNewChat}>New project chat</button></div>
      <div className="pw-actions"><button className="es-btn es-btn-sm" disabled={busy} onClick={() => importer.current?.click()}>Import report</button>
        <input ref={importer} type="file" accept=".json,application/json" hidden onChange={importFile} aria-label="Import report manifest" />
        <button className="es-btn es-btn-sm" disabled={busy || !data?.project.sharing_enabled} onClick={() => void act(async () => { const result = await request<{ imported: number; errors: { file: string; error: string }[] }>(`${base}/sync`, {}); return `${result.imported} report(s) updated.${result.errors.length ? " " + result.errors.map(e => `${e.file}: ${e.error}`).join("; ") : ""}`; })}>Sync reports</button>
        <details className="pw-sharing"><summary>Sharing {data?.project.sharing_enabled ? "on" : "off"}</summary><div><p>Share named reports through this folder. Agents keep their own permissions. Export includes project brief, saved research and artifact content; it excludes chat transcripts.</p>
          <button className="es-btn es-btn-sm" disabled={busy} onClick={() => void act(async () => { await request(`${base}/sharing`, { enabled: !data?.project.sharing_enabled }); return data?.project.sharing_enabled ? "Automatic report sync disabled." : "Shared folder prepared. Use the documented manifest convention."; })}>{data?.project.sharing_enabled ? "Disable sharing" : "Prepare shared folder"}</button>
          <button className="es-btn es-btn-sm" disabled={busy} onClick={() => void act(async () => { const result = await request<{ path: string }>(`${base}/export`, {}); return `Exported authorized project context to ${result.path}`; })}>Export project context</button>
        </div></details><span className="pw-path" title={data?.project.workspace_root}>{data?.project.workspace_root}</span>
      </div>
    </header>
    <nav className="pw-nav" aria-label="Project sections">{SECTIONS.map(name => <button type="button" key={name} aria-current={section === name ? "page" : undefined} onClick={() => { setSection(name); setSelected(null); setFile(""); setQuery(""); }}>{name}</button>)}</nav>
    {error ? <p className="pw-notice is-error" role="alert">{error}</p> : null}{message ? <p className="pw-notice" role="status">{message}</p> : null}
    <div className="pw-content">{!data ? <p role="status">Loading project resources…</p> : file ? <ProjectFile key={file} base={base} path={file} onFile={setFile} onClose={() => setFile("")} />
      : section === "Reports" ? selected ? <><button type="button" className="pw-text-link" onClick={() => setSelected(null)}>← Reports</button><Report key={`${selected.id}-${selected.revision}`} record={selected} base={base} onFile={setFile} onRecord={id => void openRecord(id)} onChat={onChat} onArtifact={onArtifact} /></>
      : <><div className="pw-section-head"><div><h2>Work worth keeping</h2><p className="st-muted">Named reports from Echo and your other agents. Completion is a claim until you verify it.</p></div><input className="st-input" aria-label="Find reports" placeholder="Find a report…" value={query} onChange={e => setQuery(e.target.value)} /></div>
        {!data.records.length ? <div className="pw-empty"><h3>Your project’s work starts here</h3><p>Import a report manifest or prepare the shared folder for Claude Code, Codex and Echo. Documents and conversations already in this project remain in their sections.</p><button type="button" className="es-btn" onClick={() => importer.current?.click()}>Import your first report</button></div> : <div className="pw-cards">{more.shown.map(row => <article key={row.id} className="pw-card">
          {row.media[0] ? <ProjectMedia base={base} path={row.media[0].path} caption={row.media[0].caption} /> : <div className="pw-card-mark" aria-hidden>↗</div>}
          <button type="button" className="pw-card-open" onClick={() => void openRecord(row.id)}><span className="pw-eyebrow">{names[row.agent]} · Revision {row.revision}</span><h3>{row.title}</h3><p>{row.summary}</p><span className="pw-badge">{row.status.replace(/_/g, " ")} · reported</span><small>{row.verification.length} check(s) · {row.remaining.length} open item(s)</small></button>
        </article>)}</div>}{data.records.length > 0 && !filteredReports.length ? <p>No reports match that search.</p> : null}{more.collapsible ? <ShowMore expanded={more.expanded} hidden={more.hidden} label="reports" onToggle={() => more.setExpanded(value => !value)} /> : null}</>
      : section === "Files" ? <><div className="pw-section-head"><h2>Project files</h2><button className="es-btn es-btn-sm" disabled={!folder} onClick={() => setFolder(folder.split("/").slice(0, -1).join("/"))}>Up a folder</button></div><p className="pw-path">{folder || "Project root"} · Up to 300 entries per folder</p><div className="pw-list">{entries.map(row => <button type="button" key={row.path} onClick={() => row.directory ? setFolder(row.path) : setFile(row.path)}><span>{row.directory ? "▸" : "·"} {row.name}</span><small>{row.directory ? "Folder" : row.path.split(".").pop()}</small></button>)}</div></>
      : section === "Conversations" ? <><h2>Project conversations</h2><div className="pw-list">{data.sessions.map(row => <button type="button" key={row.id} onClick={() => onChat(row.id)}><strong>{row.title || "Untitled conversation"}</strong><small>{new Date(row.at * 1000).toLocaleDateString()}</small></button>)}</div>{!data.sessions.length ? <p>No conversations yet. Start a project chat above.</p> : null}</>
      : section === "Artifacts" ? <><h2>Project artifacts</h2><div className="pw-list">{data.artifacts.map(row => <button type="button" key={row.id} onClick={() => onArtifact(row.id)}><span><strong>{row.title}</strong><small>{row.excerpt}</small></span><small>{row.kind} · {row.versions} version(s)</small></button>)}</div>{!data.artifacts.length ? <p>No artifacts associated with this project.</p> : null}</>
      : section === "Research" ? <><h2>Saved project research</h2><p className="st-muted">Explicitly saved findings stay here. Temporary notebooks remain with their conversations.</p>{data.research.map((finding, i) => <article key={i} className="pw-evidence"><ProjectMarkdown text={finding.notes || "Saved sources"} base={base} path="" onFile={setFile} />{finding.sources?.map((source, n) => <div key={n}><ExternalLink href={source.url}>{source.title || source.url}</ExternalLink><p>{source.text || source.excerpt}</p></div>)}</article>)}<div className="pw-list">{data.sessions.map(row => <button type="button" key={row.id} onClick={() => onChat(row.id)}><span>Open research in {row.title || "conversation"}</span><small>Chat notebook</small></button>)}</div></>
      : section === "Media" ? <><h2>Generated media</h2><p>Project-associated creations from the existing media library.</p><button className="es-btn es-btn-sm" onClick={onCreations}>Manage in Creations</button><div className="pw-gallery">{data.media.map(row => <ProjectMedia key={row.id} base={base} path={row.name} caption={row.name} assetId={row.id} mediaKind={row.media_kind} />)}</div>{!data.media.length ? <p>No generated media associated with this project. Report screenshots stay with their reports and files.</p> : null}</>
      : section === "Routines" ? <><h2>Relevant routines</h2><div className="pw-list">{data.routines.map(row => <button type="button" key={row.id} onClick={onRoutines}><strong>{row.name}</strong><small>{row.enabled ? "Enabled" : "Paused"} · {row.last_result_status || "No recorded result"}</small></button>)}</div>{!data.routines.length ? <p>No routines explicitly associated with this project or its conversations.</p> : null}</>
      : <><h2>Recorded activity</h2><p className="st-muted">Existing Echo execution records. External work is represented by imported reports, not fabricated execution logs.</p><div className="pw-list">{data.activity.map(row => <button type="button" key={row.id} onClick={() => onChat(row.session_id)}><span>{row.objective || row.id}</span><small>{row.status}</small></button>)}</div>{!data.activity.length ? <p>No recorded activity for this project.</p> : null}</>}
    </div>
  </section>;
}
