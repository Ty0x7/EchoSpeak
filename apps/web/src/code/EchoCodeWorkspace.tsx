import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageShell } from "../lean/Pages";
import { WorkspacePopover } from "../components/WorkspacePopover";
import { RuntimeMark } from "../components/RuntimeMark";
import { RuntimeUsage, latestUsage } from "./RuntimeUsage";
import { startUsageRefresh } from "./usageRefresh";
import { ExternalSession, type ExternalComposerState, type ExternalSessionHandle } from "./ExternalSession";
import { CodeModelPicker, type NativeModel } from "./CodeModelPicker";
import { CodeEffortPicker } from "./CodeEffortPicker";
import type { ProviderInfo } from "../app/types";
import { WelcomeTypewriter } from "../dashboard/WelcomeTypewriter";
import { useWorkbench } from "../workbench/store";
import { request } from "../projects/model";
import { agentModelName, agentModelRow, agentModels, canStartSession, CodeRequestError, createCodeSession, planLabel, runtimeName, runtimeStatus,
  type AgentAccount, type CodeSession, type CodeWorkspace, type RuntimeCatalog } from "./model";
import "../projects/workspace.css";
import "./code.css";

type Project = { id: string; name: string; workspace_root?: string; archived?: boolean };

/** What Code's sidebar asked to open: a runtime, and for external runtimes one of its sessions. */
export type CodeOpenRequest = { runtimeId: string; sessionId: string; nonce: number };

const NATIVE_EFFORTS = ["minimal", "low", "medium", "high", "extra_high", "max", "ultra"];

export function EchoCodeWorkspace({ apiBase, projects, projectId, onProject, onAddFolder, onSession, nativeInfo, nativeLocked, onConfigureProviders, openRequest, onSessionsChanged,
  nativeEffort = "medium", onNativeEffort }: {
  apiBase: string; projects: Project[]; projectId: string; onProject(id: string): void;
  onAddFolder(): void; onSession(session: CodeSession, draft?: string): void;
  nativeInfo: ProviderInfo | null; nativeLocked: boolean; onConfigureProviders(): void;
  openRequest?: CodeOpenRequest | null; onSessionsChanged?(): void;
  /** Echo native's reasoning effort: the same setting the session's composer uses. */
  nativeEffort?: string; onNativeEffort?(value: string): void;
}) {
  const available = projects.filter(project => !project.archived && project.workspace_root);
  const [catalog, setCatalog] = useState<RuntimeCatalog | null>(null);
  const [loaded, setLoaded] = useState<CodeWorkspace | null>(null);
  const workspaceRevision = useWorkbench(state => state.revision);
  const workspace = loaded?.project.id === projectId ? loaded : null;
  const [runtimeId, setRuntime] = useState(openRequest?.runtimeId || "echo");
  useEffect(() => { if (openRequest) setRuntime(openRequest.runtimeId); }, [openRequest]);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const restore = () => {
      try { const raw=sessionStorage.getItem("echospeak.code.restoreDraft"); if(!raw)return; const row=JSON.parse(raw); sessionStorage.removeItem("echospeak.code.restoreDraft"); onProject(row.project_id); setRuntime("echo"); setDraft(row.text); setImages([]); setNotice("Restored stash; its saved copy stays in Drafts until removed."); } catch { setError("Could not restore the stash"); }
    };
    restore(); window.addEventListener("echospeak:code-draft",restore); return()=>window.removeEventListener("echospeak:code-draft",restore);
  }, []);
  const stash = async () => {
    setError("");setBusy(true);const requestedProject=projectId;
    try { await request(`${apiBase}/echo-code/drafts`,{project_id:projectId,text:draft,images});if(mounted.current&&activeProject.current===requestedProject){setDraft("");setImages([]);setNotice("Stashed in the left sidebar");}window.dispatchEvent(new CustomEvent("echospeak:code-changed")); }
    catch(e) {setError(e instanceof Error?e.message:"Could not stash draft")}
    finally {if(mounted.current)setBusy(false)}
  };
  const addImage = (file: File) => {
    if(runtimeId!=="echo" || file.size>2000000 || images.length>=4 || !["image/png","image/jpeg","image/webp"].includes(file.type)){setError("Native Code drafts support four PNG, JPEG or WebP images below 2 MB each");return}
    const reader=new FileReader();reader.onload=()=>setImages(v=>v.length<4?[...v,String(reader.result)]:v);reader.readAsDataURL(file);
  };
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const runtimeButton = useRef<HTMLButtonElement>(null);
  const connectionButton = useRef<HTMLButtonElement>(null);
  const external = useRef<ExternalSessionHandle>(null);
  const [nativeChoice, setNativeChoice] = useState<NativeModel | null>(null);
  const [model, setModel] = useState("");
  // Each coding agent's own effort control, remembered per agent; "" = the agent's default for the model.
  const [effort, setEffortState] = useState(() => { try { return window.localStorage.getItem(`echospeak.code.effort.${openRequest?.runtimeId || "codex"}`) || ""; } catch { return ""; } });
  useEffect(() => { try { setEffortState(window.localStorage.getItem(`echospeak.code.effort.${runtimeId}`) || ""); } catch { setEffortState(""); } }, [runtimeId]);
  const setEffort = useCallback((value: string) => { setEffortState(value); try { window.localStorage.setItem(`echospeak.code.effort.${runtimeId}`, value); } catch { /* per-viewer convenience only */ } }, [runtimeId]);
  const [externalState, setExternalState] = useState<ExternalComposerState | null>(null);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [accounts, setAccounts] = useState<Record<string, AgentAccount>>({});
  const [accountRevision, setAccountRevision] = useState(0);
  const modelScope = useRef("");
  const seenSession = useRef("");
  const externalStatus = useCallback((state: ExternalComposerState) => {
    const scope = `${runtimeId}:${projectId}:${state.sessionId}`;
    if (modelScope.current !== scope) { modelScope.current = scope; setModel(""); }
    setExternalState(state); if (state.approvals) setConnectionOpen(true);
    if (state.sessionId && seenSession.current !== state.sessionId) { seenSession.current = state.sessionId; onSessionsChanged?.(); }
  }, [runtimeId, projectId, onSessionsChanged]);
  useEffect(() => { setExternalState(null); setModel(""); setConnectionOpen(false); }, [runtimeId, projectId]);
  // Each coding agent's own sign-in and plan models, independent of any session: what Echo last
  // saw (instant, survives restarts). Codex refreshes on entry and every five minutes.
  useEffect(() => {
    if (runtimeId !== "codex" && runtimeId !== "claude_cli") return;
    let live = true;
    const url = `${apiBase}/echo-code/runtimes/${runtimeId}`;
    let pending: Promise<void> | undefined;
    const check = () => pending ||= request<AgentAccount>(`${url}/check`, {}).then(fresh => { if (live) setAccounts(previous => ({ ...previous, [runtimeId]: fresh })); }).finally(() => { pending = undefined; });
    request<AgentAccount>(`${url}/account`).then(remembered => {
      if (!live) return;
      setAccounts(previous => ({ ...previous, [runtimeId]: remembered }));
      if (runtimeId === "codex" || remembered.stale || accountRevision) return check();
    }).catch(err => { if (live) setError(`Could not check ${runtimeName(runtimeId)}: ${err instanceof Error ? err.message : "runtime unavailable"}`); });
    const stop = runtimeId === "codex" ? startUsageRefresh(check, err => { if (live) setError(`Usage refresh failed: ${err instanceof Error ? err.message : "runtime unavailable"}. The last observation is retained.`); }) : () => {};
    return () => { live = false; stop(); };
  }, [apiBase, runtimeId, accountRevision]);
  const recheckAccount = useCallback(() => { window.setTimeout(() => setAccountRevision(value => value + 1), 15000); }, []);
  // Keep a creation identity after a lost response, scoped to the exact request.
  const pending = useRef<{ scope: string; key: string } | null>(null);
  const activeProject = useRef(projectId);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (runtimeId === "echo") useWorkbench.getState().setContext({ project_id: projectId, external_session_id: "" });
  }, [projectId, runtimeId]);
  activeProject.current = projectId;

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
    setLoaded(previous => previous?.project.id === projectId ? previous : null); setError("");
    if (projectId) request<CodeWorkspace>(`${apiBase}/echo-code/projects/${encodeURIComponent(projectId)}`)
      .then(value => { if (live) setLoaded(value); }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [apiBase, projectId, revision, workspaceRevision]);


  const selected = catalog?.runtimes.find(runtime => runtime.id === runtimeId);
  const start = async (background = false) => {
    if (busy || !canStartSession(selected, workspace)) return;
    const scope = `${projectId}:${runtimeId}`;
    if (pending.current?.scope !== scope) pending.current = { scope, key: crypto.randomUUID() };
    setBusy(true); setError("");
    const requestedProject = projectId;
    // Bind the selected configured endpoint to this native Code conversation.
    const choice = nativeChoice || (nativeInfo?.provider && nativeInfo.model ? { provider: nativeInfo.provider, model: nativeInfo.model } : null);
    if (!choice) { setBusy(false); setError("Choose a connected local or API model for the native harness. Coding subscriptions use Codex or Claude Code."); return; }
    try {
      const session = await createCodeSession(apiBase, projectId, runtimeId, pending.current.key);
      const binding = await request<ProviderInfo>(`${apiBase}/provider?session_id=${encodeURIComponent(session.thread_id)}`);
      await request(`${apiBase}/provider/switch`, { ...choice, session_id: session.thread_id, expected_revision: binding.binding_revision || 1 });
      let text=draft.trim();
      if(images.length) { const saved=await request<{text:string}>(`${apiBase}/echo-code/drafts`,{project_id:projectId,text,images});text=saved.text;setImages([]);setDraft(text); }
      if (background) {
        await request(`${apiBase}/echo-code/background`, {thread_id:session.thread_id,message:text,request_id:`turn:${session.thread_id}`,effort:nativeEffort});
        setDraft("");setNotice("Sent in the background · open it from Working");onSessionsChanged?.();window.dispatchEvent(new CustomEvent("echospeak:code-changed"));
      } else if (mounted.current && activeProject.current === requestedProject) onSession(session, text);
      pending.current = null;
    } catch (err) {
      if (err instanceof CodeRequestError && err.status >= 400 && err.status < 500) pending.current = null;
      if (mounted.current && activeProject.current === requestedProject) setError(err instanceof Error ? err.message : "Could not create the coding session");
    } finally { if (mounted.current) setBusy(false); }
  };

  const projectName = available.find(project => project.id === projectId)?.name;
  const working = busy || Boolean(externalState?.busy || externalState?.active);
  const rememberedAccount = accounts[runtimeId] || null;
  const account = useMemo(() => rememberedAccount
    ? { ...rememberedAccount, subscription_usage: latestUsage(rememberedAccount.subscription_usage, externalState?.subscriptionUsage) } : rememberedAccount,
    [rememberedAccount, externalState?.subscriptionUsage]);
  const agentList = externalState?.models.length ? externalState.models : agentModels(account?.models);
  const modelName = runtimeId === "echo" ? nativeChoice?.model || nativeInfo?.model || "Choose a connected model"
    : agentModelName(agentList, model, externalState?.model || "") || `${selected?.name || "Agent"} · Choose model`;
  const signedIn = Boolean(externalState?.signedIn || account?.signed_in);
  const modelRow = runtimeId === "echo" ? undefined : agentModelRow(agentList, model, externalState?.model || "");
  const agentEfforts = modelRow?.efforts || [];
  // A remembered level this model doesn't offer falls back to its default rather than being sent.
  const agentEffort = agentEfforts.includes(effort) ? effort : "";
  const nativeProfileCurrent = !nativeChoice || (nativeChoice.provider === nativeInfo?.provider && nativeChoice.model === nativeInfo?.model);
  const nativeEffortOn = nativeProfileCurrent && Boolean(nativeInfo?.model_profile?.thinking_controls?.effort);
  const connectionLabel = externalState?.connected ? "Session & permissions"
    : signedIn ? `${runtimeName(runtimeId)} · ${planLabel(account) || "Signed in"}` : runtimeId === "acp" ? "Connect agent" : `Sign in to ${runtimeName(runtimeId)}`;
  return <div className="ec-page"><PageShell title="Code" lead=""
    action={<button className="es-btn es-btn-quiet es-btn-sm" onClick={onAddFolder}>Add folder</button>}>
    <div className="ec-workspace">
      {error ? <p role="alert" className="ec-notice">{error}</p> : null}
      {notice ? <p role="status" className="ec-notice">{notice}</p> : null}
      {runtimeId !== "echo" && account ? <RuntimeUsage usage={account.subscription_usage} compact/> : null}
      <div className="ec-start">
        {runtimeId !== "echo" && externalState?.transcript ? <pre className="ec-chat-transcript" role="log" aria-label="Coding conversation">{externalState.transcript}</pre>
          : <div className="ec-welcome"><h2>What should we build{projectName ? <> in <span>{projectName}</span></> : <> together</>}?</h2>
              <WelcomeTypewriter coding /></div>}
        <div className="ec-draft-card">
          <textarea aria-label="Coding task draft" value={draft} onChange={event => setDraft(event.target.value)} disabled={working} maxLength={60000}
            onPaste={event=>{const files=Array.from(event.clipboardData.files);if(files.length){event.preventDefault();files.forEach(addImage)}}}
            onKeyDown={event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="s"){event.preventDefault();void stash()}else if((event.ctrlKey||event.metaKey)&&event.key==="Enter"){event.preventDefault();if(runtimeId==="echo"&&(draft.trim()||images.length)&&!working)void start(true);else if(runtimeId!=="echo")external.current?.send()}}}
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
                models={agentList} model={model} reportedModel={externalState?.model || ""} connected={Boolean(externalState?.connected)} signedIn={signedIn}
                account={account}
                onModel={value => { setModel(value); setRuntimeOpen(false); runtimeButton.current?.focus(); }}
                onConfigureRuntime={() => { setRuntimeOpen(false); if (projectId) setConnectionOpen(true); else setError("Choose a project below to connect this coding agent. Your draft stays here."); }} />
            </WorkspacePopover> : null}
            {runtimeId === "echo"
              ? <CodeEffortPicker options={nativeEffortOn ? NATIVE_EFFORTS : []} value={nativeEffort} defaultValue="medium" disabled={working}
                  unavailable={nativeProfileCurrent ? "This model has no reasoning control" : "Reasoning capabilities load when this model's session starts"} onChange={value => onNativeEffort?.(value)} />
              : <CodeEffortPicker options={runtimeId === "acp" ? [] : agentEfforts} value={agentEffort} defaultValue={modelRow?.defaultEffort || ""} disabled={working}
                  unavailable={runtimeId === "acp" ? "This agent manages its own effort" : "Choose a model to see its effort levels"} onChange={setEffort} />}
            {runtimeId === "echo" ? <span className="ec-native-policy" title="Your existing Echo permission rules apply.">Echo permissions</span>
              : <button ref={connectionButton} type="button" className="ec-link ec-connection-trigger" disabled={!projectId || busy}
                  aria-label="Coding agent connection" aria-expanded={connectionOpen} onClick={() => setConnectionOpen(value => !value)}>{connectionLabel}</button>}
            <button className="ec-continue" disabled={working || (runtimeId === "echo" ? !canStartSession(selected, workspace) : !externalState?.canSend || !draft.trim())}
              onClick={() => runtimeId === "echo" ? void start() : external.current?.send()}
              aria-label={runtimeId === "echo" ? "New native coding session" : `Send to ${selected?.name || "agent"}`}>
              {working ? <span>…</span> : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden><path d="M12 19V5m-6 6 6-6 6 6" /></svg>}
            </button>
          </div>
        </div>
        <div className="ec-checkout-bar">
          {runtimeId !== "echo" ? <span>{externalState?.workspaceMode === "worktree" ? "Isolated Git worktree" : "Project folder"}</span> : null}
          <div className="ec-draft-shortcuts"><button type="button" disabled={!projectId || working || (!draft.trim()&&!images.length)} onClick={()=>void stash()}>Stash · Ctrl+S</button>{runtimeId==="echo"?<label className="ec-attach-image">Attach image<input aria-label="Attach image to coding draft" type="file" disabled={working} accept="image/png,image/jpeg,image/webp" onChange={e=>{if(e.target.files?.[0])addImage(e.target.files[0]);e.target.value=""}}/></label>:null}<small>Ctrl+Enter · {runtimeId==="echo"?"send in background":"send to runtime"}</small></div>
          {images.length?<div className="ec-draft-images">{images.map((src,i)=><button type="button" key={i} title="Remove attachment" onClick={()=>setImages(v=>v.filter((_,j)=>i!==j))}><img src={src} alt={`Draft attachment ${i+1}`}/><span>×</span></button>)}</div>:null}
          <label><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="M3 7h6l2 2h10v11H3z" /></svg><select aria-label="Coding project" value={projectId} disabled={busy} onChange={event => onProject(event.target.value)}>
            <option value="">Choose project</option>{available.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select></label>
          <span title={workspace?.project.workspace_root}>{!projectId ? "Local workspace" : workspace ? (workspace.git_summary || (runtimeId === "echo" ? "Project folder · Git optional" : "Git optional"))
            : error ? "Couldn't open this project" : "Opening project…"}</span>
          <button className="ec-link" disabled={busy} onClick={() => setRevision(value => value + 1)} aria-label="Refresh workspace" title="Refresh workspace">↻</button>
        </div>
        <div className="ec-start-note" role="status">
          {selected && !externalState?.connected && (!selected.implemented || !selected.can_start) ? <><strong>{runtimeStatus(selected)}</strong><p>{selected.blockers.join(" ")}</p></>
            : runtimeId !== "echo" && runtimeId !== "acp" && projectId && !signedIn && account ? <p>{runtimeName(runtimeId)} isn't signed in. Choose “Sign in to {runtimeName(runtimeId)}” to use your plan.</p>
            : workspace?.native_blocker ? <p>{workspace.native_blocker}</p>
            : null}
          {catalog && catalog.privacy_mode !== "standard" ? <small>{catalog.privacy_mode} mode · External runtimes remain blocked.</small> : null}
        </div>
      </div>
      {runtimeId !== "echo" && projectId ? <ExternalSession key={`${projectId}:${runtimeId}:${openRequest?.runtimeId === runtimeId ? openRequest.sessionId : ""}`} ref={external} apiBase={apiBase} projectId={projectId} runtimeId={runtimeId}
        initialSessionId={openRequest?.runtimeId === runtimeId ? openRequest.sessionId : ""}
        canStart={Boolean(workspace?.can_start_native && selected?.can_start)} worktreeAvailable={workspace?.worktree_available} worktreeUnavailable={workspace?.worktree_unavailable} account={account} draft={draft} onDraft={setDraft} model={model} effort={agentEffort} onState={externalStatus}
        open={connectionOpen} anchor={connectionButton} onClose={() => setConnectionOpen(false)}
        onNeedSetup={() => setConnectionOpen(true)} onAccountChanged={recheckAccount} onRefreshAccount={() => setAccountRevision(value => value + 1)} /> : null}
    </div>
  </PageShell></div>;
}
