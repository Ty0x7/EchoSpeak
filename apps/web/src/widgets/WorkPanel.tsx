import React, { useEffect, useState } from "react";
import { creationRequest } from "../creations/api";
import "./workPanel.css";

type Work = { id: string; agent_id: string; objective: string; status: string; revision: number; verification: string; result: string; tokens: number; budget: { tokens: number }; parent_id: string; evidence: unknown[]; session_id: string; plan?: { task: string; owner: string; status: string }[] };
type Proposal = { id: string; kind: string; proposed_by: string; revision: number; payload: Record<string, unknown> };
type Responsibility = { id: string; agent_id: string; objective: string; status: string; trigger: string; next_wake: string | null };
type Delivery = { id: string; channel: string; agent_id: string; status: string; error?: string };
type Approval = { id: string; summary: string; reason: string; recovered?: boolean; kind: string; question?: string; options?: string[]; args: unknown };
type State = { works: Work[]; proposals: Proposal[]; responsibilities: Responsibility[]; deliveries: Delivery[] };

/** Durable native work lives inside the existing Activity surface. */
export function WorkPanel({ apiBase, sessionId, active }: { apiBase: string; sessionId: string; active: boolean }) {
  const [data, setData] = useState<State | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [all, setAll] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [owners, setOwners] = useState<{ id: string; name: string }[]>([]);
  const load = async () => {
    const query = all ? "" : `?session_id=${encodeURIComponent(sessionId)}`;
    const [state, pending, roster] = await Promise.all([
      creationRequest(apiBase, `/lean/work${query}`), creationRequest(apiBase, `/lean/approvals${query}`), creationRequest(apiBase, "/lean/agents"),
    ]);
    setData(state); setApprovals(pending.items.filter((a: Approval) => a.recovered)); setOwners(roster.items || roster.agents || []);
  };
  useEffect(() => {
    if (!active) return;
    let disposed = false;
    const refresh = () => { if (!disposed) void load().catch(e => { if (!disposed) setError(String(e.message)); }); };
    refresh(); const timer = window.setInterval(refresh, 5000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [apiBase, sessionId, active, all]);
  const action = async (path: string, body: unknown) => {
    setBusy(true); setError(""); setNotice("");
    try { await creationRequest(apiBase, path, "POST", body); window.dispatchEvent(new Event("echospeak:agents-changed")); setNotice("Saved. Activity will refresh."); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const workAction = (work: Work, name: string) => action(`/lean/work/${encodeURIComponent(work.id)}`, {
    action: name, revision: work.revision, session_id: all ? work.session_id : sessionId, reconciliation: answers[work.id] || "", agent_id: answers[`owner:${work.id}`] || "",
  });
  const nameOf = (id: string) => owners.find(o => o.id === id)?.name || (id.startsWith("worker:") ? "Temporary worker" : id);
  return <section className="native-work" aria-label="Agent work and reviews">
    <div className="nw-heading"><strong>Agent work</strong><label><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} />All conversations</label></div>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!data?.works.length && <p className="nw-muted">Simple conversations stay lightweight. Tasks, delegated help and routine occurrences appear here.</p>}
    {data?.works.map(work => <details key={work.id} open={undefined}>
      <summary><span>{work.objective.replace(/^This is an automated run[^\n]*\n\n/, "").slice(0, 100)}</span><small>{nameOf(work.agent_id)} · {work.status.replace(/_/g, " ")}</small></summary>
      <p className="nw-muted">{work.id}{work.parent_id && ` · Help for ${work.parent_id}`}<br />Verification: {work.verification} · {work.tokens}/{work.budget.tokens} tokens</p>
      {work.result && <p className="nw-result">{work.result}</p>}
      {!!work.plan?.length && <ol>{work.plan.map((step, i) => <li key={i}>{step.task}<small>{step.owner} · {step.status}</small></li>)}</ol>}
      {!!work.evidence?.length && <details><summary>Evidence</summary><pre>{JSON.stringify(work.evidence, null, 2)}</pre></details>}
      {work.status === "unknown" && <label>Record what happened before continuing<textarea value={answers[work.id] || ""} onChange={e => setAnswers(a => ({ ...a, [work.id]: e.target.value }))} placeholder="Inspect the affected files or outgoing message and record the outcome." /></label>}
      <div className="nw-actions">
        {!['completed', 'failed', 'cancelled'].includes(work.status) && <button disabled={busy} onClick={() => void workAction(work, "cancelled")}>Cancel work</button>}
        {work.status === "running" && <button disabled={busy} onClick={() => void workAction(work, "paused")}>Pause</button>}
        {['ready', 'paused', 'interrupted', 'blocked', 'waiting_input', 'waiting_dependency', 'unknown'].includes(work.status) && <button disabled={busy || (work.status === "unknown" && !answers[work.id]?.trim())} onClick={() => void workAction(work, "resume")}>{work.status === "unknown" ? "Resume after reconciliation" : "Resume in chat"}</button>}
        {['ready', 'paused', 'interrupted', 'blocked'].includes(work.status) && <><select aria-label="Transfer work owner" value={answers[`owner:${work.id}`] || ""} onChange={e => setAnswers(a => ({ ...a, [`owner:${work.id}`]: e.target.value }))}><option value="">Choose owner…</option>{owners.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select><button disabled={busy || !answers[`owner:${work.id}`]} onClick={() => void workAction(work, "transfer")}>Transfer ownership</button></>}
      </div>
    </details>)}
    {approvals.map(a => <details key={a.id} open><summary>{a.kind === "question" ? "Waiting for your answer" : "Recovered approval"}</summary><p>{a.summary}</p><p className="nw-muted">{a.reason}</p><pre>{JSON.stringify(a.args, null, 2)}</pre>{a.kind === "question" ? <><input aria-label="Your answer" value={answers[a.id] || ""} placeholder={a.options?.join(" / ")} onChange={e => setAnswers(v => ({ ...v, [a.id]: e.target.value }))} /><button disabled={busy || !answers[a.id]?.trim()} onClick={() => void action(`/lean/approvals/${a.id}`, { decision: answers[a.id] })}>Answer</button></> : <div className="nw-actions"><button disabled={busy} onClick={() => void action(`/lean/approvals/${a.id}`, { decision: "allow" })}>Allow exact action</button><button disabled={busy} onClick={() => void action(`/lean/approvals/${a.id}`, { decision: "deny" })}>Deny</button></div>}<small>After deciding, resume the work above. Changed actions require fresh approval.</small></details>)}
    {data?.proposals.map(p => <details key={p.id}><summary>Review {p.kind} proposal · {nameOf(p.proposed_by)}</summary><dl>{Object.entries(p.payload).map(([key, value]) => <React.Fragment key={key}><dt>{key.replace(/_/g, " ")}</dt><dd>{Array.isArray(value) ? value.join(", ") : typeof value === "object" && value ? Object.entries(value).map(([k, v]) => `${k}: ${v}`).join(" · ") : String(value)}</dd></React.Fragment>)}</dl><p className="nw-muted">Review tools, context and budgets. Accepting activates the configuration shown here.</p><div className="nw-actions"><button disabled={busy} onClick={() => void action(`/lean/agent-proposals/${p.id}`, { action: "accept", revision: p.revision })}>Accept proposal</button><button disabled={busy} onClick={() => void action(`/lean/agent-proposals/${p.id}`, { action: "decline", revision: p.revision })}>Decline</button></div></details>)}
    {!!data?.responsibilities.length && <details><summary>Ongoing responsibilities</summary>{data.responsibilities.map(r => <div key={r.id}><p>{r.objective}<br /><small>{nameOf(r.agent_id)} · {r.status} · {r.next_wake ? `Next wake ${new Date(r.next_wake).toLocaleString()}` : r.trigger === "event" ? "Waiting for an authenticated event" : "No scheduled wake"}</small></p><button disabled={busy} onClick={() => void action(`/lean/responsibilities/${r.id}`, { action: r.status === "active" ? "pause" : "resume" })}>{r.status === "active" ? "Pause responsibility" : "Resume responsibility"}</button></div>)}</details>}
    {!!data?.deliveries.length && <details><summary>Result delivery</summary>{data.deliveries.slice(-20).map(d => <p key={d.id}>{nameOf(d.agent_id)} · {d.channel} · {d.status}<small>{d.error}</small>{d.status === "failed" && <button disabled={busy} onClick={() => void action(`/lean/deliveries/${d.id}/retry`, {})}>Retry delivery only</button>}{d.status === "unknown" && <><small>Inspect the destination before deciding; automatic resend is blocked.</small><textarea aria-label="Delivery reconciliation evidence" value={answers[d.id] || ""} onChange={e => setAnswers(a => ({ ...a, [d.id]: e.target.value }))} placeholder="Record what you found at the destination." /><button disabled={busy || !answers[d.id]?.trim()} onClick={() => void action(`/lean/deliveries/${d.id}/reconcile`, { action: "confirmed", reconciliation: answers[d.id] })}>Already delivered</button><button disabled={busy || !answers[d.id]?.trim()} onClick={() => void action(`/lean/deliveries/${d.id}/reconcile`, { action: "failed", reconciliation: answers[d.id] })}>Confirmed absent: allow retry</button></>}</p>)}</details>}
  </section>;
}
