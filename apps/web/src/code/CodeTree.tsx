import React, { useEffect, useRef, useState } from "react";
import { RuntimeMark } from "../components/RuntimeMark";
import { request } from "../projects/model";
import { inboxState, runtimeName, type CodeSession, type InboxSummary } from "./model";
import { CodeControl } from "./CodeControl";
import { SidebarIcon } from "../components/SidebarIcon";

export type ExternalRow = { id: string; runtime_id: string; state: string; created_at: number; project_id: string; title?: string; branch?: string; inbox?: InboxSummary };
export type CodeProjectRow = { id: string; name: string; workspace_root: string; trust_state: string; sessions: CodeSession[]; external?: ExternalRow[] };
export type CodeOverview = { projects: CodeProjectRow[]; unorganized: CodeSession[]; drafts?: {id:string;title:string;project_id:string;project_name:string;text:string;images:string[]}[] };
export type InboxRow = { key: string; id: string; title: string; project: string; projectId: string; runtime: string; external: boolean; branch: string; at: number; inbox?: InboxSummary; native?: CodeSession };
export function canDropInto(project: Pick<CodeProjectRow, "id" | "trust_state">, fromProjectId: string): boolean {
  return project.trust_state === "trusted" && project.id !== fromProjectId;
}
export function externalTitle(row: ExternalRow): string {
  return row.title || `${runtimeName(row.runtime_id)} · ${new Date(row.created_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}${row.state === "rolled_back" ? " · rolled back" : ""}`;
}
export function InboxStatus({ title, inbox, working = false }: { title: string; inbox?: InboxSummary; working?: boolean }) {
  const state = inboxState(inbox, working);
  return <span className="ec-tree-session-copy"><span>{title}</span><small className={`ec-inbox-status is-${state}`}>{state === "needs_attention" ? "Needs attention" : state === "ready_to_review" ? "Ready to review" : state === "working" ? "Working" : state === "snoozed" ? "Snoozed" : "Settled"}{inbox?.verification ? ` · ${inbox.verification}` : ""}</small></span>;
}
export function overviewRows(data: CodeOverview | null): InboxRow[] {
  if (!data) return [];
  const rows: InboxRow[] = [];
  const add = (native: CodeSession[], project: string, projectId: string) => native.forEach(s => rows.push({ key: "native:" + s.thread_id, id: s.thread_id, title: s.title.replace(/^Code · /, "") || "Coding session", project, projectId, runtime: "echo", external: false, branch: "", at: s.inbox?.updated_at || s.last_active_at, inbox: s.inbox, native: s }));
  data.projects.forEach(p => {
    add(p.sessions, p.name, p.id);
    p.external?.forEach(s => rows.push({ key: "external:" + s.id, id: s.id, title: externalTitle(s), project: p.name, projectId: p.id, runtime: s.runtime_id, external: true, branch: s.branch || "", at: s.inbox?.updated_at || s.created_at, inbox: s.inbox }));
  });
  add(data.unorganized, "Unorganized", "");
  return rows.sort((a, b) => b.at - a.at || a.key.localeCompare(b.key));
}
export function selectRange(keys: string[], from: string, to: string): string[] {
  const a = keys.indexOf(from), b = keys.indexOf(to);
  return a < 0 || b < 0 ? [to] : keys.slice(Math.min(a, b), Math.max(a, b) + 1);
}
function saved<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) || "null") ?? fallback; } catch { return fallback; }
}
const elapsed = (at: number) => { const m = Math.max(0, Math.floor((Date.now() / 1000 - at) / 60)); return m < 1 ? "Now" : m < 60 ? `${m}m` : m < 1440 ? `${Math.floor(m / 60)}h` : `${Math.floor(m / 1440)}d`; };

export function CodeTree({ apiBase, revision, activeThreadId, activeExternalId, onOpenNative, onOpenExternal, onNewSession, onAddFolder, workingSessionIds, searchQuery }: {
  searchQuery?: string; apiBase: string; revision: number; activeThreadId: string; activeExternalId: string; workingSessionIds?: Set<string>;
  onOpenNative(session: CodeSession): void; onOpenExternal(projectId: string, runtimeId: string, sessionId: string): void;
  onNewSession(projectId: string): void; onAddFolder(): void;
}) {
  const [data, setData] = useState<CodeOverview | null>(null);
  const [error, setError] = useState("");
  const [localQuery, setQuery] = useState("");
  const query = searchQuery ?? localQuery;
  const [project, setProject] = useState("");
  const [revisionLocal, refresh] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [menu, setMenu] = useState("");
  const [saving, setSaving] = useState(false);
  const [prNumber, setPrNumber] = useState("");
  const [control, setControl] = useState(false);
  const [filters, setFilters] = useState(false);
  const [hideWorking, setHideWorking] = useState(() => saved("echospeak.code.workingShelf", true));
  const [shelves, setShelves] = useState<Record<string, boolean>>(() => saved("echospeak.code.shelves", {}));
  const anchor = useRef("");
  const lastSync = useRef(0);
  useEffect(() => { try { localStorage.setItem("echospeak.code.shelves", JSON.stringify(shelves)); localStorage.setItem("echospeak.code.workingShelf", JSON.stringify(hideWorking)); } catch {} }, [shelves, hideWorking]);
  useEffect(() => {
    let live = true, loading = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      if (loading || document.visibilityState === "hidden") return;
      loading = true;
      try { const next = await request<CodeOverview>(`${apiBase}/echo-code/overview`); if (live) setData(next);
        if(Date.now()-lastSync.current>60000){lastSync.current=Date.now();void request(`${apiBase}/echo-code/inbox/sync`,{}).catch(()=>{});}
      }
      catch (e) { if (live) setError(e instanceof Error ? e.message : "Could not load Code"); }
      finally { loading = false; if (live) timer = setTimeout(() => void load(), 4000); }
    };
    const visible = () => { clearTimeout(timer); if (document.visibilityState !== "hidden") void load(); };
    const changed = () => { clearTimeout(timer); void load(); };
    void load();
    document.addEventListener("visibilitychange", visible); window.addEventListener("echospeak:code-changed", changed);
    return () => { live = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visible); window.removeEventListener("echospeak:code-changed", changed); };
  }, [apiBase, revision, revisionLocal]);
  const all = overviewRows(data);
  const stateOf = (r: InboxRow) => inboxState(r.inbox, !r.external && workingSessionIds?.has(r.id));
  const rows = all.filter(r => (!project || r.projectId === project) && (!query || `${r.title} ${r.project} ${r.runtime} ${r.branch}`.toLowerCase().includes(query.toLowerCase())));
  const active = rows.filter(r => !["settled", "snoozed"].includes(stateOf(r)) && (!hideWorking || stateOf(r) !== "working"));
  const visibleRows = [...active, ...(hideWorking && shelves.working ? rows.filter(r=>stateOf(r)==="working") : []),
    ...(shelves.snoozed ? rows.filter(r=>stateOf(r)==="snoozed") : []), ...(shelves.settled ? rows.filter(r=>stateOf(r)==="settled").sort((a,b)=>(b.inbox?.settled_at||b.at)-(a.inbox?.settled_at||a.at)) : [])];
  const triage = async (targets: InboxRow[], action: string, seconds = 0) => {
    setSaving(true); setError("");
    try {
      const response = await request<{ results: { ok: boolean; error?: string }[] }>(`${apiBase}/echo-code/inbox/batch`, {
        targets: targets.filter(r => r.inbox).map(r => ({ id: r.id, external: r.external, activity: r.inbox!.activity })), action, snooze_seconds: seconds,
      });
      const failed = response.results.filter(r => !r.ok);
      if (failed.length) setError(`${failed.length} session(s) unchanged: ${failed[0].error}`);
      else { setSelected([]); setMenu(""); }
    } catch (e) { setError(e instanceof Error ? e.message : "Inbox update failed"); }
    finally { setSaving(false); refresh(n => n + 1); }
  };
  const open = (r: InboxRow, event: React.MouseEvent) => {
    if (event.shiftKey || event.ctrlKey || event.metaKey || selected.length) {
      setSelected(old => event.shiftKey ? [...new Set([...old, ...selectRange(visibleRows.map(r => r.key), anchor.current, r.key)])] : old.includes(r.key) ? old.filter(k => k !== r.key) : [...old, r.key]);
      anchor.current = r.key; return;
    }
    anchor.current = r.key;
    if (r.native) onOpenNative(r.native); else onOpenExternal(r.projectId, r.runtime, r.id);
  };
  const actions = (targets: InboxRow[]) => <select aria-label={targets.length === 1 ? `Inbox state for ${targets[0].title}` : "Bulk inbox action"} disabled={saving} defaultValue="" onChange={e => {
    const [action, seconds] = e.target.value.split(":"); if (action) void triage(targets, action, Number(seconds || 0)); e.target.value = "";
  }}>
    <option value="" disabled>Choose action…</option><option value="settled">Mark settled</option>
    <option value="snoozed:900">Snooze 15 minutes</option><option value="snoozed:3600">Snooze 1 hour</option><option value="snoozed:86400">Snooze 24 hours</option>
    <option value="reopen">Wake / un-settle</option>
  </select>;
  const renderRow = (r: InboxRow) => {
    const state = stateOf(r), current = r.external ? activeExternalId === r.id : activeThreadId === r.id;
    const badge = r.inbox?.woke ? "Woke" : state === "needs_attention" ? "Input" : state === "ready_to_review" ? "Review" : state === "settled" ? "Done" : state === "working" ? "Working" : "Snoozed";
    return <li key={r.key} className={`ec-inbox-row${current ? " is-active" : ""}${selected.includes(r.key) ? " is-selected" : ""}`}>
      <button type="button" className="ec-inbox-open" aria-current={current ? "true" : undefined} aria-pressed={selected.includes(r.key)} title={`${r.title} · ${r.inbox?.reason || ""} · ${r.inbox?.verification || ""}`} onClick={e => open(r, e)}>
        <span className="ec-inbox-top"><span className="ec-project-mark" aria-hidden>{r.project.slice(0, 1).toUpperCase()}</span><span className="ec-inbox-project">{r.project}</span>
          <span className={`ec-inbox-badge is-${state}${r.inbox?.woke ? " is-woke" : ""}`}><i aria-hidden>{state === "settled" ? "✓" : state === "needs_attention" ? "?" : state === "working" ? "◌" : r.inbox?.woke ? "↗" : "○"}</i>{badge}</span></span>
        <strong className="ec-inbox-title">{r.title}</strong>
        <span className="ec-inbox-bottom"><SidebarIcon name="branch"/><span className="ec-inbox-branch">{r.branch || (r.external ? "Isolated worktree" : "Project folder")}</span><RuntimeMark runtime={r.runtime} /><small>{elapsed(r.at)}</small></span>
        {r.inbox?.verification === "Checks failed" ? <span className="ec-inbox-failure">Checks failed</span> : null}
      </button>
      {r.inbox?.can_triage ? <button type="button" className="ec-inbox-menu" aria-label={`Inbox actions for ${r.title}`} onClick={() => setMenu(menu === r.key ? "" : r.key)}>⋯</button> : null}
      {menu === r.key ? <div className="ec-inbox-actions">{actions([r])}
        <div className="ec-inbox-pr"><input aria-label={`Pull request number for ${r.title}`} type="number" min="1" placeholder="PR #" value={prNumber} onChange={e=>setPrNumber(e.target.value)}/><button type="button" disabled={!prNumber||saving} onClick={async()=>{setSaving(true);try{await request(`${apiBase}/echo-code/inbox/pull-request`,{id:r.id,external:r.external,activity:r.inbox!.activity,number:Number(prNumber)});setMenu("");setPrNumber("");setError("");}catch(e){setError(e instanceof Error?e.message:"Could not link PR")}finally{setSaving(false)}}}>Link & auto-settle</button></div>
        {!r.external && data?.projects.some(p => canDropInto(p, r.projectId)) ? <select aria-label={`Move ${r.title} to project`} defaultValue="" onChange={e => {
          void request(`${apiBase}/echo-code/sessions/${encodeURIComponent(r.id)}/project`, { project_id: e.target.value }).then(() => { setMenu(""); refresh(n => n + 1); }).catch(e => setError(e.message));
        }}><option value="" disabled>Move to project…</option>{data.projects.filter(p => canDropInto(p, r.projectId)).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select> : null}
      </div> : null}
    </li>;
  };
  const shelf = (name: string, label: string, items: InboxRow[]) => <section className={`ec-inbox-shelf is-${name}${shelves[name] ? " is-expanded" : ""}`} aria-label={label}>
    {shelves[name] ? <ul id={`ec-shelf-${name}`}>{items.length ? items.map(renderRow) : <li className="ec-tree-note">No {label.toLowerCase()} sessions</li>}</ul> : null}
    <button type="button" className="ec-inbox-divider" aria-controls={`ec-shelf-${name}`} aria-expanded={Boolean(shelves[name])} onClick={() => setShelves(old => ({ [name]: !old[name] }))}><span>{label} ({items.length})</span><span aria-hidden>{shelves[name] ? "⌄" : "⌃"}</span></button>
  </section>;
  return <div className="ec-tree ec-inbox" aria-label="Code inbox">
    <div className="ec-inbox-toolbar">{searchQuery === undefined ? <><SidebarIcon name="search"/><input aria-label="Search coding sessions" placeholder="Search" value={query} onChange={e => setQuery(e.target.value)} /></> : <span className="ec-inbox-toolbar-label">Sessions</span>}
      <button type="button" title="Inbox filters" aria-label="Inbox filters" aria-expanded={filters} onClick={()=>setFilters(v=>!v)}><SidebarIcon name="filter"/></button><button type="button" title="Add project" aria-label="Add a project folder" onClick={onAddFolder}><SidebarIcon name="folder"/></button><button type="button" title="New thread" aria-label="New coding session" onClick={() => onNewSession(project || data?.projects[0]?.id || "")}><SidebarIcon name="compose"/></button></div>
    {filters ? <div className="ec-inbox-options"><select aria-label="Filter coding projects" value={project} onChange={e => setProject(e.target.value)}><option value="">All projects</option>{data?.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <label><input type="checkbox" checked={hideWorking} onChange={e=>setHideWorking(e.target.checked)}/>Working shelf</label></div> : null}
    {selected.length ? <div className="ec-inbox-selection"><span>{selected.length} selected</span>{actions(all.filter(r => selected.includes(r.key)))}<button type="button" onClick={() => setSelected([])}>Clear</button></div> : null}
    {error ? <p role="alert" className="ec-tree-note">{error}</p> : null}
    {!data && !error ? <p role="status" className="ec-tree-note">Loading Code…</p> : null}
    <div className="ec-inbox-stream">{data?.drafts?.length ? <section className="ec-inbox-drafts" aria-label="Stashed drafts"><strong>Drafts ({data.drafts.length})</strong>{data.drafts.map(d=><div key={d.id}>
      <button type="button" className="ec-inbox-open" onClick={async()=>{try{const restored=await request(`${apiBase}/echo-code/drafts/${d.id}`);sessionStorage.setItem("echospeak.code.restoreDraft",JSON.stringify(restored));onNewSession(d.project_id);window.dispatchEvent(new CustomEvent("echospeak:code-draft"))}catch(e){setError(e instanceof Error?e.message:"Could not restore draft")}}}><span className="ec-inbox-project">{d.project_name} · Draft{d.images.length?` · ${d.images.length} image(s)`:""}</span><strong>{d.title}</strong></button>
      <button type="button" className="ec-tree-action" aria-label={`Remove draft ${d.title}`} onClick={async()=>{try{const r=await fetch(`${apiBase}/echo-code/drafts/${d.id}`,{method:"DELETE"});if(!r.ok)throw new Error("Could not remove draft");refresh(v=>v+1)}catch(e){setError(e instanceof Error?e.message:"Remove failed")}}}>×</button>
    </div>)}</section> : null}
    <ul className="ec-inbox-active" aria-label="Active coding sessions">{active.map(renderRow)}</ul>
    {data && !rows.length ? <p className="ec-tree-note">{all.length ? "No matching sessions" : "Choose a project and start a thread."}</p> : null}
    </div><div className="ec-inbox-dock" aria-label="Parked coding sessions">{hideWorking ? shelf("working", "Working", rows.filter(r => stateOf(r) === "working")) : null}
    {shelf("snoozed", "Snoozed", rows.filter(r => stateOf(r) === "snoozed"))}
    {shelf("settled", "Settled", rows.filter(r => stateOf(r) === "settled").sort((a,b) => (b.inbox?.settled_at || b.at) - (a.inbox?.settled_at || a.at)))}
    </div><div className="ec-inbox-footer"><button type="button" aria-label="MCP access & diagnostics" onClick={() => setControl(v => !v)} aria-expanded={control}><SidebarIcon name="connections"/><span>Connections & diagnostics</span><small>{control ? "−" : "+"}</small></button></div>
    {control ? <div className="ec-inbox-control"><CodeControl apiBase={apiBase} projects={data?.projects || []} /></div> : null}
  </div>;
}
