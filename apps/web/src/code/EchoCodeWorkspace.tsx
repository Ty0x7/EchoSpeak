import React, { useCallback, useEffect, useRef, useState } from "react";
import { PageShell } from "../lean/Pages";
import { WorkspacePopover } from "../components/WorkspacePopover";
import { RuntimeMark } from "../components/RuntimeMark";
import { ExternalSession, type ExternalComposerState, type ExternalSessionHandle } from "./ExternalSession";
import { CodeModelPicker, type NativeModel } from "./CodeModelPicker";
import type { ProviderInfo } from "../app/types";
import { WelcomeTypewriter } from "../dashboard/WelcomeTypewriter";
import { useWorkbench } from "../workbench/store";
import { ProjectFile } from "../projects/ProjectDocument";
import { request } from "../projects/model";
import { agentModelName, canStartSession, CodeRequestError, createCodeSession, runtimeStatus, type CodeSession, type CodeWorkspace, type RuntimeCatalog } from "./model";
import "../projects/workspace.css";
import "./code.css";

type Project = { id: string; name: string; workspace_root?: string; archived?: boolean };
type Entry = { name: string; path: string; directory: boolean };

export function EchoCodeWorkspace({ apiBase, projects, projectId, onProject, onAddFolder, onSession, nativeInfo, nativeLocked, onConfigureProviders }: {
  apiBase: string; projects: Project[]; projectId: string; onProject(id: string): void;
  onAddFolder(): void; onSession(session: CodeSession, draft?: string): void;
  nativeInfo: ProviderInfo | null; nativeLocked: boolean; onConfigureProviders(): void;
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
  const connectionButton = useRef<HTMLButtonElement>(null);
  const external = useRef<ExternalSessionHandle>(null);
  const [nativeChoice, setNativeChoice] = useState<NativeModel | null>(null);
  const [model, setModel] = useState("");
  const [externalState, setExternalState] = useState<ExternalComposerState | null>(null);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const modelScope = useRef("");
  const externalStatus = useCallback((state: ExternalComposerState) => {
    const scope = `${runtimeId}:${projectId}:${state.sessionId}`;
    if (modelScope.current !== scope) { modelScope.current = scope; setModel(""); }
    setExternalState(state); if (state.approvals) setConnectionOpen(true);
  }, [runtimeId, projectId]);
  useEffect(() => { setExternalState(null); setModel(""); setConnectionOpen(false); }, [runtimeId, projectId]);
  // Keep a creation identity after a lost response, scoped to the exact request.
  const pending = useRef<{ scope: string; key: string } | null>(null);
  const activeProject = useRef(projectId);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (runtimeId === "echo") useWorkbench.getState().setContext({ project_id: projectId, external_session_id: "" });
  }, [projectId, runtimeId]);
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
      const choice = nativeChoice || (nativeInfo?.provider && nativeInfo.model ? { provider: nativeInfo.provider, model: nativeInfo.model } : null);
      if (choice) {
        const binding = await request<ProviderInfo>(`${apiBase}/provider?session_id=${encodeURIComponent(session.thread_id)}`);
        await request(`${apiBase}/provider/switch`, { ...choice, session_id: session.thread_id, expected_revision: binding.binding_revision || 1 });
      }
      pending.current = null;
      if (mounted.current && activeProject.current === requestedProject) onSession(session, draft.trim());
    } catch (err) {
      if (err instanceof CodeRequestError && err.status >= 400 && err.status < 500) pending.current = null;
      if (mounted.current && activeProject.current === requestedProject) setError(err instanceof Error ? err.message : "Could not create the coding session");
    } finally { if (mounted.current) setBusy(false); }
  };

  const projectName = available.find(project => project.id === projectId)?.name;
  const working = busy || Boolean(externalState?.busy || externalState?.active);
  const modelName = runtimeId === "echo" ? nativeChoice?.model || nativeInfo?.model || "Choose model"
    : agentModelName(externalState?.models || [], model, externalState?.model || "") || `${selected?.name || "Agent"} · Choose model`;
  return <div className="ec-page"><PageShell title="Echo Code" lead=""
    action={<><button className="es-btn es-btn-quiet es-btn-sm" aria-expanded={showFiles} onClick={() => setShowFiles(value => !value)}>Files &amp; sessions</button><button className="es-btn es-btn-quiet es-btn-sm" onClick={onAddFolder}>Add folder</button></>}>
    <div className="ec-workspace">
      {error ? <p role="alert" className="ec-notice">{error}</p> : null}
      <div className="ec-start">
        {runtimeId !== "echo" && externalState?.transcript ? <pre className="ec-chat-transcript" role="log" aria-label="Coding conversation">{externalState.transcript}</pre>
          : <div className="ec-welcome"><h2>What should we build{projectName ? <> in <span>{projectName}</span></> : <> together</>}?</h2>
              <WelcomeTypewriter coding /></div>}
        <div className="ec-draft-card">
          <textarea aria-label="Coding task draft" value={draft} onChange={event => setDraft(event.target.value)} disabled={working} maxLength={60000}
            placeholder="Describe a change, ask about your code, or plan what to build" rows={1}
            onInput={event => { const field=event.currentTarget; field.style.height="auto"; field.style.height=`${Math.min(field.scrollHeight,156)}px`; }} />
          <div className="ec-draft-tools">
            <button className="ec-runtime-picker" ref={runtimeButton} type="button" aria-label="Agent runtime" aria-haspopup="dialog" aria-expanded={runtimeOpen}
              disabled={working || !catalog} title={`${selected?.name || "Agent"} · ${modelName}`} onClick={() => setRuntimeOpen(value => !value)}>
              <RuntimeMark runtime={runtimeId} /><span className="ec-model-label">{modelName}</span><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="m6 9 6 6 6-6" /></svg>
            </button>
            {runtimeOpen && catalog ? <WorkspacePopover anchor={runtimeButton} label="Choose coding model" width={420} className="es-model-popover" onClose={() => setRuntimeOpen(false)}>
              <CodeModelPicker apiBase={apiBase} catalog={catalog} runtimeId={runtimeId} onRuntime={setRuntime}
                nativeInfo={nativeInfo} nativeChoice={nativeChoice} nativeLocked={nativeLocked}
                onNative={choice => { setNativeChoice(choice); setRuntimeOpen(false); runtimeButton.current?.focus(); }}
                onConfigureNative={() => { setRuntimeOpen(false); onConfigureProviders(); }}
                models={externalState?.models || []} model={model} reportedModel={externalState?.model || ""} connected={Boolean(externalState?.connected)}
                onModel={value => { setModel(value); setRuntimeOpen(false); runtimeButton.current?.focus(); }}
                onConfigureRuntime={() => { setRuntimeOpen(false); if (projectId) setConnectionOpen(true); else setError("Choose a project below to connect this coding agent. Your draft stays here."); }} />
            </WorkspacePopover> : null}
            {runtimeId === "echo" ? <span className="ec-native-policy" title="Your existing Echo permission rules apply.">Echo permissions</span>
              : <button ref={connectionButton} type="button" className="ec-link ec-connection-trigger" disabled={!projectId || busy}
                  aria-label="Coding agent connection" aria-expanded={connectionOpen} onClick={() => setConnectionOpen(value => !value)}>{externalState?.connected ? "Session & permissions" : `Connect ${selected?.name || "agent"}`}</button>}
            <button className="ec-continue" disabled={working || (runtimeId === "echo" ? !canStartSession(selected, workspace) : !externalState?.canSend || !draft.trim())}
              onClick={() => runtimeId === "echo" ? void start() : external.current?.send()}
              aria-label={runtimeId === "echo" ? "New native coding session" : `Send to ${selected?.name || "agent"}`}>
              {working ? <span>…</span> : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden><path d="M12 19V5m-6 6 6-6 6 6" /></svg>}
            </button>
          </div>
        </div>
        <div className="ec-checkout-bar">
          <label><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="M3 7h6l2 2h10v11H3z" /></svg><select aria-label="Coding project" value={projectId} disabled={busy} onChange={event => onProject(event.target.value)}>
            <option value="">Choose project</option>{available.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select></label>
          <span title={workspace?.project.workspace_root}>{workspace?.git_summary || (projectId ? "Opening project…" : "Local workspace")}</span>
          <button className="ec-link" disabled={busy} onClick={() => setRevision(value => value + 1)} aria-label="Refresh workspace" title="Refresh workspace">↻</button>
        </div>
        <div className="ec-start-note" role="status">
          {selected && !externalState?.connected && (!selected.implemented || !selected.can_start) ? <><strong>{runtimeStatus(selected)}</strong><p>{selected.blockers.join(" ")}</p></>
            : workspace?.native_blocker ? <p>{workspace.native_blocker}</p>
            : null}
          {catalog && catalog.privacy_mode !== "standard" ? <small>{catalog.privacy_mode} mode · External runtimes remain blocked.</small> : null}
        </div>
      </div>
      {runtimeId !== "echo" && projectId ? <ExternalSession key={`${projectId}:${runtimeId}`} ref={external} apiBase={apiBase} projectId={projectId} runtimeId={runtimeId}
        canStart={Boolean(workspace?.can_start_native && selected?.can_start)} draft={draft} onDraft={setDraft} model={model} onState={externalStatus}
        open={connectionOpen} anchor={connectionButton} onClose={() => setConnectionOpen(false)} /> : null}
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
