import React, { useEffect, useRef, useState } from "react";
import { request } from "../projects/model";
import { RuntimeMark } from "../components/RuntimeMark";
import { useWorkbench } from "../workbench/store";

type Event = { seq: number; kind: string; data: Record<string, any> };
type Session = {
  id: string; runtime_id: string; state: string; worktree: string; native_id: string | null;
  created_at: number; connected: boolean; cursor: number; replay_gap: boolean;
  account: { connected: boolean; type: string }; models: { id: string; model: string; displayName?: string }[];
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
  if (event.kind === "sdk/event") {
    if (data.kind === "StreamEvent") return String(data.data?.event?.delta?.text || "");
    if (data.kind === "ResultMessage" && data.data?.is_error) return "\nClaude reported a failed turn.\n";
  }
  if (event.kind === "user/message") return `\nYou: ${String(data.text || "")}\n\n`;
  if (event.kind === "session/error") return `\n${String(data.message || "Runtime failed")}\n`;
  if (event.kind === "turn/finished") return `\n\n[${String(data.state || "finished")}]\n`;
  if (event.kind === "permission_denied") return `\n${String(data.reason || "Permission denied")}\n`;
  return "";
}

export function ExternalSession({ apiBase, projectId, runtimeId, canStart, initialDraft = "" }: {
  apiBase: string; projectId: string; runtimeId: string; canStart: boolean; initialDraft?: string;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [text, setText] = useState(initialDraft);
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
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
  const base = `${apiBase}/echo-code/external-sessions/${encodeURIComponent(selected)}`;
  const active = ["preparing", "running"].includes(session?.state || "");
  const name = runtimeId === "codex" ? "Codex" : runtimeId === "claude_code" ? "Claude Agent" : "ACP agent";
  useEffect(() => {
    useWorkbench.getState().setContext({ project_id: projectId, external_session_id: selected });
  }, [projectId, selected]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let live = true;
    request<Session[]>(`${apiBase}/echo-code/projects/${encodeURIComponent(projectId)}/external-sessions`)
      .then(rows => { if (live) setSessions(rows.filter(row => row.runtime_id === runtimeId)); })
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
  const create = () => action(async () => {
    let command: string[] = [];
    if (runtimeId === "acp") {
      const values: unknown = JSON.parse(args);
      if (!Array.isArray(values) || !values.every(item => typeof item === "string")) throw new Error("ACP arguments must be a JSON array of strings");
      command = [executable, ...values];
    }
    const context = JSON.stringify({ command, dirty, projectId, runtimeId });
    if (pending.current.context !== context) pending.current = { context, key: crypto.randomUUID() };
    const result = await request<Session>(`${apiBase}/echo-code/projects/${encodeURIComponent(projectId)}/external-sessions`, {
      runtime_id: runtimeId, idempotency_key: pending.current.key, command, acknowledge_dirty: dirty, acknowledge_execution: acknowledge,
    });
    if (!alive.current) return;
    pending.current = { key: crypto.randomUUID(), context: "" };
    setSessions(previous => [result, ...previous.filter(row => row.id !== result.id)]); setSelected(result.id);
  });
  return <section className="ec-external" aria-label={`${name} sessions`}>
    <header><RuntimeMark runtime={runtimeId} /><strong>{name}</strong><select aria-label="External coding session" value={selected} disabled={busy}
      onChange={event => setSelected(event.target.value)}><option value="">New isolated session</option>{sessions.map(row =>
      <option key={row.id} value={row.id}>{new Date(row.created_at * 1000).toLocaleString()} · {row.id.slice(0, 7)}</option>)}</select></header>
    {error ? <p role="alert" className="ec-notice">{error}</p> : null}
    {!selected ? <div className="ec-runtime-setup">
      <p>Start in a separate Git worktree from committed HEAD. Your current checkout and local changes stay where they are.</p>
      {runtimeId === "acp" ? <><label>ACP executable<input value={executable} onChange={event => setExecutable(event.target.value)} placeholder="Absolute path to a native executable" /></label>
        <label>Arguments (JSON array)<input value={args} onChange={event => setArgs(event.target.value)} /></label>
        <small>ACP v1 over stdio. Authenticate the agent in its own terminal first. Filesystem and terminal client capabilities are not exposed.</small></> : null}
      {runtimeId === "claude_code" ? <small>Requires the optional official Claude Agent SDK and a separate Anthropic API key. Subscription login is not offered.</small> : null}
      <label className="ec-check"><input type="checkbox" checked={acknowledge} onChange={event => setAcknowledge(event.target.checked)} />
        I trust this executable and project. The harness runs tools as my OS account; worktrees do not sandbox it. Command approvals may grant access beyond the worktree.</label>
      <label className="ec-check"><input type="checkbox" checked={dirty} onChange={event => setDirty(event.target.checked)} />Start from committed HEAD even if my source checkout has local changes.</label>
      <button className="es-btn es-btn-primary" disabled={busy || !canStart || !acknowledge} onClick={() => void create()}>Create isolated session</button>
    </div> : <>
      <div className="ec-session-state"><span>{session?.state || "Opening…"} · {session?.account.type || "Own account"}</span>
        {session?.native_id ? <small title={session.native_id}>Session {session.native_id.slice(0, 12)}</small> : null}</div>
      <p className="ec-worktree-path">{session?.worktree}</p>
      {!session?.connected ? <div className="ec-connect">
        {runtimeId === "claude_code" ? <label>Anthropic API key (this connection only)<input type="password" autoComplete="off" value={apiKey}
          onChange={event => setApiKey(event.target.value)} placeholder="Separate Claude harness key" /></label> : null}
        <button className="es-btn" disabled={busy || !session || session.state === "rolled_back"} onClick={() => void action(async () => {
          await request(`${base}/connect`, { api_key: apiKey }); setApiKey("");
        })}>Connect {name}</button>
      </div> : <div className="ec-session-actions">
        {runtimeId === "codex" && !session.account.connected ? <button className="es-btn" disabled={busy} onClick={() => void action(async () => {
          const result = await request<{ url: string }>(`${base}/login`, {}); setLoginUrl(result.url);
        })}>Sign into Codex</button> : null}
        <button className="es-btn" disabled={busy} onClick={() => void action(() => request(`${base}/stop`, {}))}>Stop runtime</button>
      </div>}
      {loginUrl && !session?.account.connected ? <a href={loginUrl} target="_blank" rel="noopener noreferrer">Continue official Codex sign-in ↗</a> : null}
      {session?.state === "rolled_back" ? <p>Files were restored. Start a new session; the original agent history is retained and cannot be resumed against restored files.</p> : null}
      {session?.approvals.map(approval => <div className="ec-approval" key={approval.id} role="group" aria-label="Runtime permission request">
        <strong>{name} requests permission</strong><small>{approval.kind}</small><pre>{JSON.stringify(approval.details, null, 2)}</pre>
        <button className="es-btn" disabled={busy} onClick={() => void action(() => request(`${base}/approvals/${approval.id}`, { allow: false }))}>Deny</button>
        <button className="es-btn" disabled={busy} onClick={() => void action(() => request(`${base}/approvals/${approval.id}`, { allow: true }))}>Approve once</button>
      </div>)}
      <pre className="ec-runtime-stream" role="log" aria-label="Runtime activity">{events.map(eventText).join("") || "Your agent's streamed responses and command output will appear here."}</pre>
      {session?.connected ? <form className="ec-runtime-composer" onSubmit={event => { event.preventDefault(); void action(async () => {
        await request(`${base}/turns`, { text, model }); setText("");
      }); }}>
        <textarea aria-label="External coding task" rows={1} value={text} onChange={event => setText(event.target.value)} disabled={active || busy} placeholder={`Ask ${name} to work in its isolated worktree`} />
        <div>{runtimeId === "codex" ? <select aria-label="Codex model" value={model} onChange={event => setModel(event.target.value)} disabled={active || busy}>
          <option value="">Codex default model</option>{session.models.map(item => <option key={item.id} value={item.model}>{item.displayName || item.model}</option>)}</select>
          : runtimeId === "claude_code" ? <input aria-label="Claude model" placeholder="Claude default model" value={model} onChange={event => setModel(event.target.value)} disabled={active || busy} /> : <small>Agent's own model and permissions</small>}
          <button className="es-btn" type="submit" disabled={busy || active || !text.trim() || (runtimeId === "codex" && !session.account.connected)}>Send to {name}</button></div>
      </form> : null}
      <div className="ec-session-actions"><button className="es-btn" disabled={busy || !session} onClick={() => void action(async () => setDiff(await request(`${base}/diff`)))}>Inspect changes</button>
        <button className="es-btn" disabled={busy || !session?.rollback_available || session?.connected} onClick={() => void action(() => request(`${base}/rollback`, {}))}>Roll back last turn</button>
        <small>Stop before rollback. Newer edits are preserved; a recovery copy is retained.</small></div>
      {diff ? <details className="ec-runtime-diff" open><summary>Worktree changes</summary><pre>{diff.status || "Clean worktree"}</pre><pre>{diff.diff}</pre><small>{diff.note}</small></details> : null}
    </>}
  </section>;
}
