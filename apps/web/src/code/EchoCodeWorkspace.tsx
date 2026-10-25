import React, { useEffect, useRef, useState } from "react";
import { PageShell } from "../lean/Pages";
import { WorkspacePopover } from "../components/WorkspacePopover";
import { RuntimeMark } from "../components/RuntimeMark";
import { ExternalSession } from "./ExternalSession";
import { ProjectFile } from "../projects/ProjectDocument";
import { request } from "../projects/model";
import { canStartSession, CodeRequestError, createCodeSession, runtimeStatus, type CodeSession, type CodeWorkspace, type RuntimeCatalog } from "./model";
import "../projects/workspace.css";
import "./code.css";

type Project = { id: string; name: string; workspace_root?: string; archived?: boolean };
type Entry = { name: string; path: string; directory: boolean };

export function EchoCodeWorkspace({ apiBase, projects, projectId, onProject, onAddFolder, onSession }: {
  apiBase: string; projects: Project[]; projectId: string; onProject(id: string): void;
  onAddFolder(): void; onSession(session: CodeSession, draft?: string): void;
}) {
  const available = projects.filter(project => !project.archived && project.workspace_root);
  const [catalog, setCatalog] = useState<RuntimeCatalog | null>(null);
  const [loaded, setLoaded] = useState<CodeWorkspace | null>(null);
  const workspace = loaded?.project.id === projectId ? loaded : null;
  const [runtimeId, setRuntime] = useState("echo");
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [folder, setFolder] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [file, setFile] = useState("");
  const [fileError, setFileError] = useState("");
  const [listing, setListing] = useState(false);
  const [draft, setDraft] = useState("");
  const [showFiles, setShowFiles] = useState(false);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const runtimeButton = useRef<HTMLButtonElement>(null);
  // Keep a creation identity after a lost response, scoped to the exact request.
  const pending = useRef<{ scope: string; key: string } | null>(null);
  const activeProject = useRef(projectId);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  activeProject.current = projectId;
  const fileBase = `${apiBase}/projects/${encodeURIComponent(projectId)}/workspace`;

  useEffect(() => {
    let live = true;
    setCatalog(null); setError("");
    request<RuntimeCatalog>(`${apiBase}/echo-code/runtimes`).then(value => {
      if (live) setCatalog(value);
    }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [apiBase, revision]);

  useEffect(() => {
    let live = true;
    setLoaded(null); setError("");
    if (projectId) request<CodeWorkspace>(`${apiBase}/echo-code/projects/${encodeURIComponent(projectId)}`)
      .then(value => { if (live) setLoaded(value); }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [apiBase, projectId, revision]);

  useEffect(() => { setFolder(""); setFile(""); }, [projectId]);
  useEffect(() => {
    let live = true;
    setEntries([]); setFileError(""); setListing(Boolean(projectId));
    if (projectId) request<{ items: Entry[] }>(`${fileBase}/files?path=${encodeURIComponent(folder)}`)
      .then(value => { if (live) setEntries(value.items); })
      .catch(err => { if (live) setFileError(err.message); })
      .finally(() => { if (live) setListing(false); });
    return () => { live = false; };
  }, [fileBase, folder, revision, projectId]);

  const selected = catalog?.runtimes.find(runtime => runtime.id === runtimeId);
  const start = async () => {
    if (busy || !canStartSession(selected, workspace)) return;
    const scope = `${projectId}:${runtimeId}`;
    if (pending.current?.scope !== scope) pending.current = { scope, key: crypto.randomUUID() };
    setBusy(true); setError("");
    const requestedProject = projectId;
    try {
      const session = await createCodeSession(apiBase, projectId, runtimeId, pending.current.key);
      pending.current = null;
      if (mounted.current && activeProject.current === requestedProject) onSession(session, draft.trim());
    } catch (err) {
      if (err instanceof CodeRequestError && err.status >= 400 && err.status < 500) pending.current = null;
      if (mounted.current && activeProject.current === requestedProject) setError(err instanceof Error ? err.message : "Could not create the coding session");
    } finally { if (mounted.current) setBusy(false); }
  };

  const projectName = available.find(project => project.id === projectId)?.name;
  return <div className="ec-page"><PageShell title="Echo Code" lead=""
    action={<><button className="es-btn es-btn-quiet es-btn-sm" aria-expanded={showFiles} onClick={() => setShowFiles(value => !value)}>Files &amp; sessions</button><button className="es-btn es-btn-quiet es-btn-sm" onClick={onAddFolder}>Add folder</button></>}>
    <div className="ec-workspace">
      {error ? <p role="alert" className="ec-notice">{error}</p> : null}
      <div className={`ec-start${runtimeId !== "echo" ? " ec-start-external" : ""}`}>
        {runtimeId === "echo" ? <>
        <div className="ec-welcome"><h2>What should we build{projectName ? <> in <span>{projectName}</span></> : <> together</>}?</h2>
          <p>{projectName ? "Make a change, explore your code, or work through an idea." : "Choose a project folder to get started."}</p></div>
        </> : null}<div className="ec-draft-card">
          {runtimeId === "echo" ?
          <textarea aria-label="Coding task draft" value={draft} onChange={event => setDraft(event.target.value)} disabled={busy}
            placeholder="Describe a change, ask about your code, or plan what to build" rows={1}
            onInput={event => { const field=event.currentTarget; field.style.height="auto"; field.style.height=`${Math.min(field.scrollHeight,156)}px`; }} />
          : null}<div className="ec-draft-tools">
            <button className="ec-runtime-picker" ref={runtimeButton} type="button" aria-label="Agent runtime" aria-haspopup="dialog" aria-expanded={runtimeOpen}
              disabled={busy || !catalog} onClick={() => setRuntimeOpen(value => !value)}>
              <RuntimeMark runtime={runtimeId} />
              {selected?.name || "Agent runtime"}<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="m6 9 6 6 6-6" /></svg>
            </button>
            {runtimeOpen ? <WorkspacePopover anchor={runtimeButton} label="Choose agent runtime" width={310} onClose={() => setRuntimeOpen(false)}>
              <div className="es-picker-heading">Agent runtime</div>{catalog?.runtimes.map(runtime => <button key={runtime.id} type="button" className="es-picker-option es-runtime-option"
                aria-pressed={runtime.id === runtimeId} onClick={() => { setRuntime(runtime.id); setRuntimeOpen(false); runtimeButton.current?.focus(); }}>
                <RuntimeMark runtime={runtime.id} /><span><strong>{runtime.name}</strong><small>{runtimeStatus(runtime)}</small></span>{runtime.id === runtimeId ? <i aria-hidden>✓</i> : null}
              </button>)}
              <footer>Each runtime keeps its own tools, sessions, and sign-in.</footer>
            </WorkspacePopover> : null}
            {runtimeId === "echo" ? <><span className="ec-native-policy" title="Your existing Echo permission rules apply. No permission settings are changed.">Echo permissions</span>
            <button className="ec-continue" disabled={busy || !canStartSession(selected, workspace)} onClick={() => void start()}
              aria-label={selected?.id === "echo" ? "New native coding session" : "Execution planned"} title={selected?.id === "echo" ? "Open a native coding session with this draft" : "External runtime execution is not implemented"}>
              {busy ? <span>…</span> : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M12 19V5m-6 6 6-6 6 6" /></svg>}
            </button></> : <span className="ec-native-policy">Own harness · separate sessions</span>}
          </div>
        </div>
        <div className="ec-checkout-bar">
          <label><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="M3 7h6l2 2h10v11H3z" /></svg><select aria-label="Coding project" value={projectId} disabled={busy} onChange={event => onProject(event.target.value)}>
            <option value="">Choose project</option>{available.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select></label>
          <span title={workspace?.project.workspace_root}>{workspace?.git_summary || (projectId ? "Opening project…" : "Local workspace")}</span>
          <button className="ec-link" disabled={busy} onClick={() => setRevision(value => value + 1)} aria-label="Refresh workspace" title="Refresh workspace">↻</button>
        </div>
        {runtimeId !== "echo" && projectId ? <ExternalSession key={`${projectId}:${runtimeId}`} apiBase={apiBase} projectId={projectId} runtimeId={runtimeId}
          canStart={Boolean(workspace?.can_start_native && selected?.can_start)} initialDraft={draft} /> : null}
        <div className="ec-start-note" role="status">
          {selected && (!selected.implemented || !selected.can_start) ? <><strong>{runtimeStatus(selected)}</strong><p>{selected.blockers.join(" ")}</p></>
            : workspace?.native_blocker ? <p>{workspace.native_blocker}</p>
            : runtimeId === "echo" ? <p>Continue with Echo's own harness. Your text stays a draft until you choose a model and send.</p> : null}
          {catalog && catalog.privacy_mode !== "standard" ? <small>{catalog.privacy_mode} mode · External runtimes remain blocked.</small> : null}
        </div>
      </div>
      {showFiles ? <div className="ec-columns">
        <section className="ec-files" aria-label="Coding project files">
          <header><h2>Files</h2>{folder ? <button className="ec-link" onClick={() => setFolder(folder.split("/").slice(0, -1).join("/"))}>Up one folder</button> : null}</header>
          <p className="ec-path">{folder || "Project root"}</p>
          {fileError ? <p role="alert">{fileError}</p> : listing ? <p role="status">Loading files…</p> : !entries.length ? <p className="ec-empty">{projectId ? "No previewable files in this folder." : "Choose a project to see its files."}</p> :
            <ul>{entries.map(entry => <li key={entry.path}><button onClick={() => entry.directory ? setFolder(entry.path) : setFile(entry.path)}>
              <span aria-hidden>{entry.directory ? "▸" : "·"}</span>{entry.name}{entry.directory ? "/" : ""}
            </button></li>)}</ul>}
          <small>Read-only preview. Private, linked, and generated paths are excluded.</small>
        </section>
        <div className="ec-main">
          <section className="ec-sessions" aria-label="Coding sessions"><h2>Coding sessions</h2>
            {workspace?.sessions.length ? <ul>{workspace.sessions.map(session => <li key={session.thread_id}>
              <button className="ec-link" onClick={() => onSession(session)}>{session.title}</button><small>Echo native · {session.message_count} messages</small>
            </li>)}</ul> : <p className="ec-empty">Your native coding sessions for this project appear here.</p>}
          </section>
        </div>
      </div> : null}
      {file && workspace ? <div className="ec-preview"><ProjectFile key={`${projectId}:${file}:${revision}`} base={fileBase} path={file} onFile={setFile} onClose={() => setFile("")} /></div> : null}
    </div>
  </PageShell></div>;
}
