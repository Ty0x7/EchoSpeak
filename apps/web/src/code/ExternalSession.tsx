import React, { useEffect, useImperativeHandle, useRef, useState } from "react";
import { request } from "../projects/model";
import { RuntimeMark } from "../components/RuntimeMark";
import { WorkspacePopover } from "../components/WorkspacePopover";
import { agentModels, planLabel, runtimeName, type AgentAccount, type AgentModel } from "./model";
import { useWorkbench } from "../workbench/store";
import { isDesktopRuntime, openDesktopExternalUrl } from "../desktop/bridge";

type Event = { seq: number; kind: string; data: Record<string, any> };
type Session = {
  model?: string;
  turn_active?: boolean;
  id: string; runtime_id: string; state: string; worktree: string; native_id: string | null;
  created_at: number; connected: boolean; cursor: number; replay_gap: boolean;
  account: { connected: boolean; type: string }; models: AgentModel[];
  approvals: { id: string; kind: string; details: unknown }[]; events: Event[]; rollback_available: boolean;
};

/** Runtime text stays text: no HTML, commands, or links are executed by rendering. */
export function eventText(event: Event): string {
  const data = event.data;
  if (event.kind === "item/agentMessage/delta" || event.kind === "item/commandExecution/outputDelta") return String(data.delta || "");
  if (event.kind === "session/update") {
    const update = data.update || {};
    if (update.sessionUpdate === "agent_message_chunk") return String(update.content?.text || "");
    if (update.sessionUpdate === "tool_call" || update.sessionUpdate === "tool_call_update") return String(update.title || update.status || "Tool activity");
  }
  if (event.kind === "claude/text") return String(data.text || "");
  if (event.kind === "claude/tool") return `\n› ${String(data.tool || "Tool")} ${String(data.detail || "")}\n`;
  if (event.kind === "claude/result" && data.is_error) return "\nClaude Code reported a failed turn.\n";
  if (event.kind === "user/message") return `\nYou: ${String(data.text || "")}\n\n`;
  if (event.kind === "session/error") return `\n${String(data.message || "Runtime failed")}\n`;
  if (event.kind === "turn/finished") return `\n\n[${String(data.state || "finished")}]\n`;
  if (event.kind === "permission_denied") return `\n${String(data.reason || "Permission denied")}\n`;
  return "";
}

/** One confirmation per project and agent lets Echo Code create its isolated worktree sessions. */
const trustKey = (projectId: string, runtimeId: string) => `echospeak.code.trusted.${projectId}.${runtimeId}`;
export function trustedFor(projectId: string, runtimeId: string): boolean {
  try { return localStorage.getItem(trustKey(projectId, runtimeId)) === "1"; } catch { return false; }
}
function rememberTrust(projectId: string, runtimeId: string) {
  try { localStorage.setItem(trustKey(projectId, runtimeId), "1"); } catch { /* Ask again next time. */ }
}

export type ExternalComposerState = {
  sessionId: string; connected: boolean; signedIn: boolean; canSend: boolean; busy: boolean; active: boolean;
  models: AgentModel[]; model: string; approvals: number; transcript: string; account: string;
};
export type ExternalSessionHandle = { send(): void };
export const ExternalSession = React.forwardRef<ExternalSessionHandle, {
  apiBase: string; projectId: string; runtimeId: string; canStart: boolean; account: AgentAccount | null;
  draft: string; onDraft(value: string): void; model: string; onState(state: ExternalComposerState): void;
  open: boolean; anchor: React.RefObject<HTMLButtonElement>; onClose(): void; onNeedSetup(): void; onAccountChanged(): void;
}>(function ExternalSession({ apiBase, projectId, runtimeId, canStart, account, draft, onDraft, model, onState, open, anchor, onClose, onNeedSetup, onAccountChanged }, ref) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [acknowledge, setAcknowledge] = useState(() => trustedFor(projectId, runtimeId));
  const [dirty, setDirty] = useState(false);
  const [executable, setExecutable] = useState("");
  const [args, setArgs] = useState("[]");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loginUrl, setLoginUrl] = useState("");
  const [diff, setDiff] = useState<{ status: string; diff: string; note: string } | null>(null);
  const cursor = useRef(0);
  const pending = useRef({ key: crypto.randomUUID(), context: "" });
  const alive = useRef(true);
  const baseFor = (id: string) => `${apiBase}/echo-code/external-sessions/${encodeURIComponent(id)}`;
  const base = baseFor(selected);
  const active = Boolean(session?.turn_active) || ["preparing", "running"].includes(session?.state || "");
  const name = runtimeName(runtimeId);
  const signedIn = runtimeId === "acp" ? Boolean(session?.connected) : Boolean(session?.connected ? session.account.connected : account?.signed_in);
  // Sending creates and connects a session when needed; ACP still needs its executable chosen first.
  const ready = session?.connected ? (runtimeId !== "codex" || session.account.connected)
    : runtimeId !== "acp" && canStart && signedIn && session?.state !== "rolled_back";
  const canSend = Boolean(ready && !active && !busy && draft.trim());
  const models = session?.connected && session.models?.length ? agentModels(session.models) : agentModels(account?.models);
  useEffect(() => {
    onState({ sessionId: selected, connected: Boolean(session?.connected), signedIn, canSend, busy, active, models,
      model: session?.model || "", approvals: session?.approvals.length || 0, transcript: events.map(eventText).join(""),
      account: session?.connected ? session.account.type : planLabel(account) });
  }, [selected, session, signedIn, canSend, busy, active, onState, events, account]);
  useEffect(() => {
    useWorkbench.getState().setContext({ project_id: projectId, external_session_id: selected });
  }, [projectId, selected]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let live = true;
    request<Session[]>(`${apiBase}/echo-code/projects/${encodeURIComponent(projectId)}/external-sessions`)
      .then(rows => {
        if (!live) return;
        const mine = rows.filter(row => row.runtime_id === runtimeId).sort((a, b) => b.created_at - a.created_at);
        setSessions(mine);
        // Continue where you left off: the newest session that wasn't rolled back.
        setSelected(current => current || mine.find(row => row.state !== "rolled_back")?.id || "");
      })
      .catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [apiBase, projectId, runtimeId]);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    cursor.current = 0; setSession(null); setEvents([]); setDiff(null); setLoginUrl("");
    if (!selected) return;
    const poll = async () => {
      try {
        const value = await request<Session>(`${base}?cursor=${cursor.current}`);
        if (!live) return;
        setSession(value); setEvents(previous => [...previous, ...value.events].slice(-600)); cursor.current = value.cursor;
        if (value.replay_gap) setError("Earlier activity left the replay window. Native history remains in the original harness.");
      } catch (err) { if (live) setError(err instanceof Error ? err.message : "Runtime disconnected"); }
      if (live) timer = setTimeout(poll, 850);
    };
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [selected, base]);
  const action = async (work: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await work(); }
    catch (err) { if (alive.current) setError(err instanceof Error ? err.message : "Runtime request failed"); }
    finally { if (alive.current) setBusy(false); }
  };
  const createSession = async (): Promise<string> => {
    let command: string[] = [];
    if (runtimeId === "acp") {
      const values: unknown = JSON.parse(args);
      if (!Array.isArray(values) || !values.every(item => typeof item === "string")) throw new Error("ACP arguments must be a JSON array of strings");
      command = [executable, ...values];
    }
    const context = JSON.stringify({ command, dirty, projectId, runtimeId });
    if (pending.current.context !== context) pending.current = { context, key: crypto.randomUUID() };
    const result = await request<Session>(`${apiBase}/echo-code/projects/${encodeURIComponent(projectId)}/external-sessions`, {
      runtime_id: runtimeId, idempotency_key: pending.current.key, command, acknowledge_dirty: dirty, acknowledge_execution: true,
    });
    rememberTrust(projectId, runtimeId);
    pending.current = { key: crypto.randomUUID(), context: "" };
    if (alive.current) { setSessions(previous => [result, ...previous.filter(row => row.id !== result.id)]); setSelected(result.id); }
    return result.id;
  };
  useImperativeHandle(ref, () => ({ send() {
    if (!canSend) return;
    const text = draft;
    if (!selected && !trustedFor(projectId, runtimeId)) { onNeedSetup(); return; }
    void action(async () => {
      // Signed in is enough: Echo starts (or reconnects) the isolated session, then sends.
      const id = selected || await createSession();
      if (!session?.connected || session.id !== id) await request(`${baseFor(id)}/connect`, {});
      await request(`${baseFor(id)}/turns`, { text, model: runtimeId === "acp" ? "" : model });
      if (alive.current) onDraft("");
    });
  } }), [canSend, draft, model, selected, session, runtimeId, projectId, onDraft, onNeedSetup]);
  const create = () => action(async () => { if (!acknowledge) throw new Error("Confirm you trust this agent with this project first"); await createSession(); });
  const openLogin = (url: string) => {
    setLoginUrl(url);
    if (isDesktopRuntime()) void openDesktopExternalUrl(url); else window.open(url, "_blank", "noopener,noreferrer");
  };
  return open ? <WorkspacePopover anchor={anchor} label={`${name} connection and session`} width={620} className="ec-connection-popover" onClose={onClose}><section className="ec-external" aria-label={`${name} sessions`}>
    <header><RuntimeMark runtime={runtimeId} /><strong>{name}</strong><select aria-label="External coding session" value={selected} disabled={busy}
      onChange={event => setSelected(event.target.value)}><option value="">New isolated session</option>{sessions.map(row =>
      <option key={row.id} value={row.id}>{new Date(row.created_at * 1000).toLocaleString()} · {row.id.slice(0, 7)}</option>)}</select></header>
    {error ? <p role="alert" className="ec-notice">{error}</p> : null}
    {runtimeId !== "acp" ? <p className="ec-account-line">{signedIn ? `Signed in${planLabel(account) ? ` · ${planLabel(account)}` : ""} · ${name} uses its own harness and your plan.`
      : `${name} isn't signed in. Sign in with ${name}'s own login, then send your task.`}
      {!signedIn ? <button className="es-btn es-btn-sm" disabled={busy} onClick={() => void action(async () => {
        const result = await request<{ url?: string }>(`${apiBase}/echo-code/runtimes/${runtimeId}/login`, {});
        if (result.url) openLogin(result.url);
        onAccountChanged();
      })}>Sign in to {name}</button> : null}</p> : null}
    {loginUrl && !signedIn ? <a href={loginUrl} target="_blank" rel="noopener noreferrer" onClick={event => {
      if (!isDesktopRuntime()) return;
      event.preventDefault();
      void openDesktopExternalUrl(loginUrl);
    }}>Continue official {name} sign-in ↗</a> : null}
    {!selected ? <div className="ec-runtime-setup">
      <p>{name} works in a separate Git worktree from committed HEAD. Your current checkout and local changes stay where they are.</p>
      {runtimeId === "acp" ? <><label>ACP executable<input value={executable} onChange={event => setExecutable(event.target.value)} placeholder="Absolute path to a native executable" /></label>
        <label>Arguments (JSON array)<input value={args} onChange={event => setArgs(event.target.value)} /></label>
        <small>ACP v1 over stdio. Authenticate the agent in its own terminal first. Filesystem and terminal client capabilities are not exposed.</small></> : null}
      {runtimeId === "claude_cli" ? <small>Runs your installed Claude Code with your own sign-in and plan. Claude Code keeps its tools; Echo asks you before any action Claude Code would ask about. Edits stay in the worktree.</small> : null}
      <label className="ec-check"><input type="checkbox" checked={acknowledge} onChange={event => setAcknowledge(event.target.checked)} />
        I trust {name} with this project. It runs tools as my Windows account; worktrees don't sandbox it, and approved commands can reach beyond the worktree. Remembered for this project.</label>
      <label className="ec-check"><input type="checkbox" checked={dirty} onChange={event => setDirty(event.target.checked)} />Start from committed HEAD even if my source checkout has local changes.</label>
      <button className="es-btn es-btn-primary" disabled={busy || !canStart || !acknowledge} onClick={() => void create()}>Create isolated session</button>
    </div> : <>
      <div className="ec-session-state"><span>{session?.state || "Opening…"} · {session?.account.type || "Own account"}</span>
        {session?.native_id ? <small title={session.native_id}>Session {session.native_id.slice(0, 12)}</small> : null}</div>
      <p className="ec-worktree-path">{session?.worktree}</p>
      {!session?.connected ? <div className="ec-connect">
        <button className="es-btn" disabled={busy || !session || session.state === "rolled_back"} onClick={() => void action(() => request(`${base}/connect`, {}))}>Connect {name}</button>
      </div> : <div className="ec-session-actions">
        {runtimeId === "codex" && !session.account.connected ? <button className="es-btn" disabled={busy} onClick={() => void action(async () => {
          const result = await request<{ url: string }>(`${base}/login`, {}); openLogin(result.url);
        })}>Sign in to Codex</button> : null}
        <button className="es-btn" disabled={busy} onClick={() => void action(() => request(`${base}/stop`, {}))}>Stop runtime</button>
      </div>}
      {session?.state === "rolled_back" ? <p>Files were restored. Start a new session; the original agent history is retained and cannot be resumed against restored files.</p> : null}
      {session?.approvals.map(approval => <div className="ec-approval" key={approval.id} role="group" aria-label="Runtime permission request">
        <strong>{name} requests permission</strong><small>{approval.kind}</small><pre>{JSON.stringify(approval.details, null, 2)}</pre>
        <button className="es-btn" disabled={busy} onClick={() => void action(() => request(`${base}/approvals/${approval.id}`, { allow: false }))}>Deny</button>
        <button className="es-btn" disabled={busy} onClick={() => void action(() => request(`${base}/approvals/${approval.id}`, { allow: true }))}>Approve once</button>
      </div>)}
      <pre className="ec-runtime-stream" role="log" aria-label="Runtime activity">{events.map(eventText).join("") || "Your agent's streamed responses and command output will appear here."}</pre>
      <div className="ec-session-actions"><button className="es-btn" disabled={busy || !session} onClick={() => void action(async () => setDiff(await request(`${base}/diff`)))}>Inspect changes</button>
        <button className="es-btn" disabled={busy || !session?.rollback_available || session?.connected} onClick={() => void action(() => request(`${base}/rollback`, {}))}>Roll back last turn</button>
        <small>Stop before rollback. Newer edits are preserved; a recovery copy is retained.</small></div>
      {diff ? <details className="ec-runtime-diff" open><summary>Worktree changes</summary><pre>{diff.status || "Clean worktree"}</pre><pre>{diff.diff}</pre><small>{diff.note}</small></details> : null}
    </>}
  </section></WorkspacePopover> : null;
});
