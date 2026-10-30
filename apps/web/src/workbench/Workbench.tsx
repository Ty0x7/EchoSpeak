import React, { useEffect, useRef, useState } from "react";
import { WorkspacePopover } from "../components/WorkspacePopover";
import { RuntimeMark } from "../components/RuntimeMark";
import { runtimeName } from "../code/model";
import { request } from "../projects/model";
import { isDesktopRuntime, openDesktopExternalUrl } from "../desktop/bridge";
import { contextKey, previewUrl, surfaceTitle, useWorkbench, workbenchUrl, type SurfaceKind } from "./store";
import { WorkbenchIcon } from "./icons";
import { TerminalDock } from "./Terminal";
import "./workbench.css";

type Change = { path: string; status: string };
type Summary = { name: string; root: string; source_root: string; runtime: string; computer: string; system: string;
  git: { is_git: boolean; branch: string; head: string; repository: string; branches: string[]; changes: Change[] };
  scripts: { id: string; name: string; command: string }[] };
type Review = { review_id: string; action: string; branch: string; paths: string[]; message: string; workspace: string; remote: string; head: string };

function useResource<T>(apiBase: string, resource: string, extra: Record<string, string> = {}) {
  const context = useWorkbench(state => state.context);
  const revision = useWorkbench(state => state.revision);
  const key = JSON.stringify(extra);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true; setData(null); setError("");
    if (context.project_id) request<T>(workbenchUrl(apiBase, context, resource, JSON.parse(key)))
      .then(value => { if (live) setData(value); }).catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [apiBase, context.project_id, context.external_session_id, revision, resource, key]);
  return { data, error };
}

export function WorkbenchControls({ apiBase }: { apiBase: string }) {
  const store = useWorkbench();
  const anchor = useRef<HTMLButtonElement>(null);
  return <div className="wb-layout-controls" role="group" aria-label="Workspace layout">
    <button ref={anchor} aria-label="Workspace details" title="Workspace details" aria-expanded={store.detailsOpen} onClick={store.toggleDetails}><WorkbenchIcon kind="details" /></button>
    <button aria-label="Toggle bottom terminal" title="Toggle terminal (Ctrl+`)" aria-pressed={store.bottomOpen} onClick={store.toggleBottom}><WorkbenchIcon kind="bottom" /></button>
    <button aria-label="Toggle workspace sidebar" title="Toggle workspace sidebar" aria-pressed={store.rightOpen} onClick={store.toggleRight}><WorkbenchIcon kind="right" /></button>
    {store.detailsOpen ? <WorkspacePopover anchor={anchor} label="Workspace details" width={380} onClose={() => useWorkbench.setState({ detailsOpen: false })}>
      <WorkspaceDetails apiBase={apiBase} />
    </WorkspacePopover> : null}
  </div>;
}

/** One collapsible section of Workspace details; open or closed is remembered per section. */
function DetailSection({ id, title, meta, children }: { id: string; title: string; meta?: React.ReactNode; children: React.ReactNode }) {
  const key = `echospeak.workbench.details.${id}`;
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(key) !== "closed"; } catch { return true; } });
  const toggle = () => setOpen(value => { try { localStorage.setItem(key, value ? "closed" : "open"); } catch { /* optional */ } return !value; });
  return <section className="wb-dsec" data-open={open ? "true" : "false"}>
    <button type="button" className="wb-dsec-head" aria-expanded={open} onClick={toggle}>
      <strong>{title}</strong><svg className="wb-dsec-caret" width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M6 9h12l-6 7z" /></svg>
      {meta ? <span className="wb-dsec-meta">{meta}</span> : null}
    </button>
    {open ? <div className="wb-dsec-body">{children}</div> : null}
  </section>;
}

/** "+128 −40" from a unified diff. */
export function diffStat(diff: string): { added: number; removed: number } {
  let added = 0, removed = 0;
  for (const line of String(diff || "").split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added += 1;
    else if (line.startsWith("-") && !line.startsWith("---")) removed += 1;
  }
  return { added, removed };
}

type ArtifactRow = { id: string; title?: string; kind?: string; updated_at?: number };

/**
 * Workspace details: where the work is happening and what it produced, at a glance.
 * Environment (computer, project, branch, runtime) · Context (how full the conversation is) · Outputs
 * (artifacts) · Changes (diff and pull requests) · Servers & scripts. Each section folds away.
 */
export function WorkspaceDetails({ apiBase }: { apiBase: string }) {
  const store = useWorkbench();
  const { data, error } = useResource<Summary>(apiBase, "summary");
  const git = Boolean(data?.git.is_git);
  const { data: diff } = useResource<{ files: Change[]; diff: string }>(apiBase, git ? "diff" : "summary");
  const { data: prs } = useResource<{ available: boolean; items: { number: number; title: string; url: string; state: string; isDraft: boolean }[] }>(apiBase, git ? "pull-requests" : "summary");
  const meter = store.meter;
  const [artifacts, setArtifacts] = useState<ArtifactRow[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let live = true;
    if (!meter.session_id) { setArtifacts([]); return; }
    request<{ items: ArtifactRow[] }>(`${apiBase}/lean/artifacts?session_id=${encodeURIComponent(meter.session_id)}`)
      .then(value => { if (live) setArtifacts((value.items || []).slice().sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))); })
      .catch(() => { if (live) setArtifacts([]); });
    return () => { live = false; };
  }, [apiBase, meter.session_id, store.revision]);
  const action = async (work: () => Promise<unknown>) => { setNotice(""); try { await work(); } catch (err) { setNotice(err instanceof Error ? err.message : "Workspace action failed"); } };
  const stat = diffStat(diff && "diff" in diff ? diff.diff : "");
  const changed = git && diff && "files" in diff ? diff.files.length : 0;
  const pct = meter.window > 0 ? Math.min(100, Math.round((meter.used / meter.window) * 100)) : 0;
  const terminals = store.terminalIds[contextKey(store.context)] || [];
  const shownArtifacts = showAll ? artifacts : artifacts.slice(0, 4);
  const openPr = (url: string) => { if (!/^https:\/\/github\.com\//.test(url)) return; if (isDesktopRuntime()) void openDesktopExternalUrl(url); else window.open(url, "_blank", "noopener,noreferrer"); };
  return <div className="wb-details wb-details-v2">
    <DetailSection id="environment" title="Environment">
      <div className="wb-drow"><WorkbenchIcon kind="device" /><span>{data?.computer || "This computer"}</span>
        <button className="wb-dlink" disabled={!data} onClick={() => void action(() => request(`${apiBase}/workbench/editor`, store.context))}>Open in VS Code</button></div>
      <div className="wb-drow" title={data?.root || ""}><WorkbenchIcon kind="files" />
        {store.context.project_id ? <span className="wb-dtrunc"><b>{data?.name || "Opening project…"}</b>{data?.root ? <small> · {data.root}</small> : null}{store.context.external_session_id ? <small> · agent worktree</small> : null}</span>
          : <span className="wb-dmuted">No project</span>}
        <button className="wb-dlink" onClick={() => window.dispatchEvent(new CustomEvent("echospeak:workbench-add-folder"))}>Add folder</button></div>
      <div className="wb-drow"><WorkbenchIcon kind="pull_requests" />
        <span className="wb-dtrunc">{!data ? "—" : git ? (data.git.branch === "HEAD" ? "Detached agent worktree" : data.git.branch) : "No Git repository"}</span>
        {data?.git.head ? <small className="wb-dmuted">{data.git.head.slice(0, 7)}</small> : null}</div>
      <div className="wb-drow"><RuntimeMark runtime={data?.runtime || "echo"} /><span>{runtimeName(data?.runtime)}</span>
        <button className="wb-dlink" onClick={() => store.open("usage")}>Usage &amp; limits</button></div>
    </DetailSection>
    <DetailSection id="context" title="Context" meta={meter.window ? <><span>{pct}%</span><i className="wb-dmeter"><i style={{ width: `${pct}%` }} data-level={pct > 85 ? "full" : pct > 60 ? "filling" : "ok"} /></i></> : null}>
      {meter.window ? <p className="wb-dnote">About {Math.round(meter.used / 1000)}k of {Math.round(meter.window / 1000)}k tokens{meter.model ? ` · ${meter.model}` : ""}. Long conversations are summarized automatically as they fill up.</p>
        : <p className="wb-dnote">Open a conversation to see how much of the model's context it uses.</p>}
    </DetailSection>
    <DetailSection id="outputs" title="Outputs" meta={artifacts.length ? <span>{artifacts.length}</span> : null}>
      {shownArtifacts.map(item => <button key={item.id} className="wb-drow wb-drow-btn" onClick={() => window.dispatchEvent(new CustomEvent("echospeak:open-artifact", { detail: { id: item.id } }))}>
        <WorkbenchIcon kind="file" /><span className="wb-dtrunc">{item.title || item.id}</span><small className="wb-dmuted">{item.kind || "Artifact"}</small></button>)}
      {artifacts.length > 4 ? <button className="wb-dmore" onClick={() => setShowAll(value => !value)}>{showAll ? "Show less" : "Show more"}</button> : null}
      {!artifacts.length ? <p className="wb-dnote">Artifacts made in this conversation appear here.</p> : null}
    </DetailSection>
    <DetailSection id="changes" title="Changes" meta={git && (stat.added || stat.removed) ? <><span className="wb-dadd">+{stat.added}</span><span className="wb-ddel">−{stat.removed}</span></> : null}>
      {!git ? <p className="wb-dnote">{data ? "This folder isn't a Git repository." : "Choose a project to see its changes."}</p> : <>
        <button className="wb-drow wb-drow-btn" onClick={() => store.open("diff")}><WorkbenchIcon kind="diff" /><span>{changed ? `${changed} changed file${changed === 1 ? "" : "s"}` : "No uncommitted changes"}</span>
          <small className="wb-dmuted">Review &amp; commit</small></button>
        {(prs && "items" in prs ? prs.items : []).slice(0, 4).map(pr => <button key={pr.number} className="wb-drow wb-drow-btn" onClick={() => openPr(pr.url)}>
          <WorkbenchIcon kind="pull_requests" /><span className="wb-dtrunc">#{pr.number} {pr.title}</span><small className="wb-dstate" data-state={pr.isDraft ? "DRAFT" : pr.state}>{pr.isDraft ? "Draft" : pr.state.toLowerCase()}</small></button>)}
        <button className="wb-dmore" onClick={() => store.open("pull_requests")}>All pull requests</button>
      </>}
    </DetailSection>
    <DetailSection id="servers" title="Servers & scripts" meta={terminals.length ? <span>{terminals.length} running</span> : null}>
      {terminals.map((id, index) => <button key={id} className="wb-drow wb-drow-btn" onClick={() => useWorkbench.setState({ bottomOpen: true })}>
        <WorkbenchIcon kind="terminal" /><span>Terminal {index + 1}</span><small className="wb-dlive">running</small></button>)}
      {data?.scripts.map(script => <button key={script.id} className="wb-drow wb-drow-btn" title={script.command} onClick={() => void action(async () => {
        const terminal = await request<{ id: string }>(`${apiBase}/workbench/scripts/${script.id}/run`, store.context); store.addTerminal(terminal.id, store.context); useWorkbench.setState({ bottomOpen: true });
      })}><WorkbenchIcon kind="terminal" /><span className="wb-dtrunc">{script.name}</span><small className="wb-dmuted">Run</small></button>)}
      {!terminals.length && !data?.scripts.length ? <p className="wb-dnote">Dev servers and scripts you run here show up in this list.</p> : null}
      <button className="wb-dmore" disabled={!data} onClick={() => setEditing(value => !value)}>{editing ? "Cancel" : "Add a project script"}</button>
      {editing ? <form className="wb-script-form" onSubmit={event => { event.preventDefault(); void action(async () => {
        await request(`${apiBase}/workbench/scripts`, { ...store.context, name, command }); setEditing(false); setName(""); setCommand(""); store.refresh();
      }); }}><label>Script name<input required value={name} onChange={event => setName(event.target.value)} maxLength={80} /></label>
        <label>Command<input required value={command} onChange={event => setCommand(event.target.value)} placeholder="npm run dev" maxLength={2000} /></label>
        <button type="submit" className="es-btn">Save script</button><small>Saving does not run the script.</small></form> : null}
    </DetailSection>
    {error || notice ? <p className="wb-error" role="alert">{error || notice}</p> : null}
  </div>;
}

const SURFACES: SurfaceKind[] = ["browser", "terminal", "files", "diff", "pull_requests", "linked_prs", "device", "research", "activity", "usage"];
export function SurfacePicker({ nativeAllowed = true }: { nativeAllowed?: boolean }) {
  const store = useWorkbench();
  return <div className="wb-surface-picker" onKeyDown={event => { if (event.ctrlKey || event.metaKey || event.altKey) return; const index = ["b", "t", "f", "d", "p", "l", "m", "r", "a", "u"].indexOf(event.key.toLowerCase()); if (index >= 0) { event.preventDefault(); event.currentTarget.querySelectorAll<HTMLButtonElement>("button")[index]?.click(); } }}><strong>Open a surface</strong>{SURFACES.map((kind, index) => <button key={kind}
    disabled={!nativeAllowed && !store.context.external_session_id && ["research", "activity"].includes(kind)} onClick={() => {
      if (kind === "terminal") useWorkbench.setState({ bottomOpen: false });
      store.open(kind);
    }}><WorkbenchIcon kind={kind} /><span>{surfaceTitle[kind]}</span><kbd>{["B", "T", "F", "D", "P", "L", "M", "R", "A", "U"][index]}</kbd></button>)}</div>;
}

export function WorkbenchTabs({ onNativeTab, nativeAllowed = true }: { onNativeTab(kind: string): void; nativeAllowed?: boolean }) {
  const store = useWorkbench();
  const [picker, setPicker] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  return <div className="wb-tabs" role="tablist" aria-label="Workspace surfaces" onKeyDown={event => {
    const index = store.tabs.findIndex(tab => tab.id === store.active);
    const next = event.key === "ArrowRight" ? (index + 1) % store.tabs.length : event.key === "ArrowLeft" ? (index + store.tabs.length - 1) % store.tabs.length : -1;
    if (next >= 0) { event.preventDefault(); const tab = store.tabs[next]; store.select(tab.id); onNativeTab(tab.kind); }
  }}>{store.tabs.map(tab => <div className="wb-tab" key={tab.id}><button role="tab" aria-selected={store.active === tab.id} onClick={() => { store.select(tab.id); onNativeTab(tab.kind); }}>
      <WorkbenchIcon kind={tab.kind} /><span>{tab.title}</span></button><button className="wb-tab-close" aria-label={`Close ${tab.title}`} onClick={() => store.close(tab.id)}>×</button></div>)}
    <button ref={anchor} className="wb-new-surface" aria-label="Open a surface" title="Open a surface" aria-expanded={picker} onClick={() => setPicker(value => !value)}>＋</button>
    {picker ? <WorkspacePopover anchor={anchor} label="Open a surface" width={260} onClose={() => setPicker(false)}><div onClick={() => setPicker(false)}><SurfacePicker nativeAllowed={nativeAllowed} /></div></WorkspacePopover> : null}
  </div>;
}

function FileTree({ apiBase, path = "", level = 0, search = "" }: { apiBase: string; path?: string; level?: number; search?: string }) {
  const { data, error } = useResource<{ items: { name: string; path: string; directory: boolean }[] }>(apiBase, "files", { path });
  const [expanded, setExpanded] = useState<string[]>([]);
  const open = useWorkbench(state => state.open);
  return <div role={level ? "group" : "tree"} aria-label={level ? undefined : "Project files"}>
    {error ? <p role="alert" className="wb-error">{error}</p> : null}
    {data?.items.filter(item => item.directory || !search || item.name.toLowerCase().includes(search.toLowerCase())).map(item => <div key={item.path}>
      <button className="wb-tree-row" style={{ paddingLeft: 12 + level * 15 }} role="treeitem" aria-expanded={item.directory ? expanded.includes(item.path) : undefined}
        onClick={() => item.directory ? setExpanded(rows => rows.includes(item.path) ? rows.filter(row => row !== item.path) : [...rows, item.path]) : open("file", { path: item.path, title: item.name })}>
        <span className="wb-tree-chevron" aria-hidden>{item.directory ? expanded.includes(item.path) ? "⌄" : "›" : ""}</span><WorkbenchIcon kind={item.directory ? "files" : "file"} /><span>{item.name}</span></button>
      {item.directory && expanded.includes(item.path) && level < 12 ? <FileTree apiBase={apiBase} path={item.path} level={level + 1} search={search} /> : null}
    </div>)}
  </div>;
}

function Files({ apiBase }: { apiBase: string }) {
  const [search, setSearch] = useState("");
  const refresh = useWorkbench(state => state.refresh);
  return <div className="wb-files"><div className="wb-search-row"><button aria-label="Refresh files" onClick={refresh}>↻</button><input aria-label="Search files" placeholder="Search files in expanded folders" value={search} onChange={event => setSearch(event.target.value)} /></div>
    <FileTree apiBase={apiBase} search={search} /><small className="wb-footnote">Private, generated and linked paths are excluded.</small></div>;
}

function FileView({ apiBase, path }: { apiBase: string; path: string }) {
  const { data, error } = useResource<{ path: string; text: string }>(apiBase, "file", { path });
  const context = useWorkbench(state => state.context);
  const [notice, setNotice] = useState("");
  return <div className="wb-file"><header><span>{path}</span><button className="es-btn es-btn-sm" onClick={() => void request(`${apiBase}/workbench/editor`, { ...context, path }).catch(err => setNotice(err.message))}>Open in VS Code ↗</button></header>
    {error || notice ? <p role="alert" className="wb-error">{error || notice}</p> : null}<pre>{data?.text || ""}</pre></div>;
}

function Changes({ apiBase }: { apiBase: string }) {
  const store = useWorkbench();
  const { data, error } = useResource<{ files: Change[]; diff: string; fingerprint: string }>(apiBase, "diff");
  const { data: summary } = useResource<Summary>(apiBase, "summary");
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [branch, setBranch] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const action = async (work: () => Promise<unknown>) => { setBusy(true); setNotice(""); try { await work(); } catch (err) { setNotice(err instanceof Error ? err.message : "Git operation failed"); } finally { setBusy(false); } };
  const prepare = (kind: string) => action(async () => setReview(await request<Review>(`${apiBase}/workbench/git/review`, { ...store.context, action: kind, paths: selected, message, branch })));
  return <div className="wb-changes"><header><WorkbenchIcon kind="pull_requests" /><strong>{summary?.git.branch === "HEAD" ? "Detached worktree" : summary?.git.branch || "Changes"}</strong><button aria-label="Refresh changes" onClick={store.refresh}>↻</button></header>
    {error || notice ? <p role="alert" className="wb-error">{error || notice}</p> : null}
    <div className="wb-change-list">{data?.files.map(row => <label key={row.path}><input type="checkbox" checked={selected.includes(row.path)} onChange={event => setSelected(items => event.target.checked ? [...items, row.path] : items.filter(path => path !== row.path))} /><code>{row.status}</code><span>{row.path}</span></label>)}{data && !data.files.length ? <p className="wb-muted">No previewable changes.</p> : null}</div>
    <pre className="wb-diff">{data?.diff || "Select a changed file in Files to preview untracked text."}</pre>
    <form onSubmit={event => { event.preventDefault(); void prepare("commit"); }}><label>Commit message<input value={message} onChange={event => setMessage(event.target.value)} placeholder="Describe the selected changes" maxLength={2000} /></label>
      <div className="wb-action-row"><button className="es-btn" disabled={busy || !selected.length || !message.trim()}>Review commit</button><button className="es-btn" type="button" disabled={busy || !summary?.git.is_git} onClick={() => void prepare("push")}>Review push</button></div></form>
    {!store.context.external_session_id ? <details><summary>Branches</summary><select aria-label="Existing Git branch" value={branch} onChange={event => setBranch(event.target.value)}><option value="">Select a branch</option>{summary?.git.branches.map(name => <option key={name}>{name}</option>)}</select>
      <button className="es-btn" disabled={!branch || busy} onClick={() => void prepare("switch")}>Review switch</button>
      <input aria-label="New branch name" placeholder="codex/my-change" value={branch} onChange={event => setBranch(event.target.value)} /><button className="es-btn" disabled={!branch || busy} onClick={() => void prepare("create_branch")}>Review new branch</button></details> : null}
    {review ? <div className="wb-git-review" role="group" aria-label="Confirm Git action"><strong>Review {review.action.replace("_", " ")}</strong><p>{review.workspace}</p><p>{review.branch} · {review.head.slice(0, 10)}</p>{review.remote ? <p>{review.remote}</p> : null}{review.paths.length ? <pre>{review.paths.join("\n")}</pre> : null}{review.message ? <p>{review.message}</p> : null}
      <button className="es-btn" disabled={busy} onClick={() => setReview(null)}>Cancel</button><button className="es-btn" disabled={busy} onClick={() => void action(async () => {
        const result = await request<{ output: string }>(`${apiBase}/workbench/git/apply`, { ...store.context, review_id: review.review_id }); setNotice(result.output || "Git action completed"); setReview(null); setSelected([]); store.refresh();
      })}>Confirm {review.action.replace("_", " ")}</button></div> : null}
    <small className="wb-footnote">Only selected files are committed. Close workspace terminals and stop its coding runtime before a Git mutation. No force push or automatic merge.</small>
  </div>;
}

function Browser({ apiBase }: { apiBase: string }) {
  const [address, setAddress] = useState("http://127.0.0.1:3000");
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [allowed, setAllowed] = useState(false);
  useEffect(() => {
    let live = true;
    const update = async () => { try { const value = await request<{ mode: string }>(`${apiBase}/privacy/status`); if (live) { setAllowed(value.mode === "standard"); if (value.mode !== "standard") setUrl(""); } } catch { if (live) { setAllowed(false); setUrl(""); } } };
    void update(); const timer = setInterval(update, 1000);
    return () => { live = false; clearInterval(timer); };
  }, [apiBase]);
  return <div className="wb-browser"><form onSubmit={event => { event.preventDefault(); const safe = previewUrl(address, apiBase); setError(safe ? "" : "Embed a loopback dev-server URL on a port separate from Echo's API."); setUrl(safe || ""); }}>
    <input aria-label="Preview URL" value={address} onChange={event => setAddress(event.target.value)} /><button className="es-btn" type="submit" disabled={!allowed}>Open</button></form>
    <button className="wb-external-link" onClick={() => {
      try { const target = new URL(address); if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) throw new Error();
        if (isDesktopRuntime()) void openDesktopExternalUrl(target.href).catch(err => setError(err.message)); else window.open(target.href, "_blank", "noopener,noreferrer");
      } catch { setError("Use an HTTP or HTTPS URL without embedded credentials"); }
    }}>Open in browser ↗</button>
    {error ? <p className="wb-error" role="alert">{error}</p> : null}
    {url && allowed ? <iframe key={url} title="Isolated project preview" src={url} sandbox="allow-scripts" referrerPolicy="no-referrer" /> : <div className="wb-empty"><WorkbenchIcon kind="browser" /><strong>Project preview</strong><p>{allowed ? "Run your dev server in a terminal, then open its loopback address here. External pages open in your browser." : "Project previews require Standard privacy mode."}</p><small>Dev servers and external browsers manage their own network access.</small></div>}
  </div>;
}

function PullRequests({ apiBase, linked }: { apiBase: string; linked: boolean }) {
  const { data, error } = useResource<{ available: boolean; reason?: string; items: { number: number; title: string; url: string; state: string; isDraft: boolean; headRefName: string }[] }>(apiBase, "pull-requests");
  const { data: summary } = useResource<Summary>(apiBase, "summary");
  const [search, setSearch] = useState("");
  const [state, setState] = useState("ALL");
  const rows = data?.items.filter(row => (!linked || row.headRefName === summary?.git.branch) && (state === "ALL" || row.state === state) && `${row.number} ${row.title} ${row.headRefName}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="wb-prs"><div className="wb-search-row"><input aria-label="Search pull requests" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search pull requests" /><select aria-label="Filter pull requests" value={state} onChange={event => setState(event.target.value)}><option>ALL</option><option>OPEN</option><option>MERGED</option><option>CLOSED</option></select><button aria-label="Refresh pull requests" onClick={useWorkbench.getState().refresh}>↻</button></div>
    {error ? <p className="wb-error" role="alert">{error}</p> : null}{data && !data.available ? <p className="wb-muted">{data.reason}</p> : null}
    {rows?.map(row => <a key={row.number} className="wb-pr-row" href={/^https:\/\/github\.com\//.test(row.url) ? row.url : undefined} target="_blank" rel="noopener noreferrer"
      onClick={event => { if (isDesktopRuntime()) { event.preventDefault(); if (/^https:\/\/github\.com\//.test(row.url)) void openDesktopExternalUrl(row.url); } }}>
      <WorkbenchIcon kind="pull_requests" /><span><strong>#{row.number} {row.title}</strong><small>{row.headRefName}</small></span><small>{row.isDraft ? "Draft" : row.state}</small></a>)}
    {data?.available && !rows?.length ? <p className="wb-muted">No matching pull requests.</p> : null}
  </div>;
}

export function Usage({ apiBase }: { apiBase: string }) {
  const { data, error } = useResource<{ runtimes: { runtime: string; session_id: string; limits?: any; usage?: Record<string, unknown>; unavailable?: string; scope?: string; reported_cost_usd?: number | null; observed_at?: number }[]; note: string; diagnostics?: { resources: { cpu_percent?: number; memory_total_bytes?: number; memory_available_bytes?: number; backend_rss_bytes?: number } } }>(apiBase, "usage");
  return <div className="wb-usage"><header><strong>Usage &amp; limits</strong><button aria-label="Refresh usage" onClick={useWorkbench.getState().refresh}>↻</button></header>
    {error ? <p role="alert" className="wb-error">{error}</p> : null}{data?.runtimes.map(row => {
      const windows = row.limits?.rateLimitsByLimitId ? Object.values(row.limits.rateLimitsByLimitId) as any[] : row.limits?.rateLimits ? [row.limits.rateLimits] : [];
      return <section key={row.session_id}><div className="wb-runtime-line"><RuntimeMark runtime={row.runtime} /><strong>{runtimeName(row.runtime)}</strong><small>{row.session_id.slice(0, 8)}</small></div>
        {windows.flatMap((limit, index) => ["primary", "secondary"].map(kind => { const window = limit[kind]; return window && typeof window.usedPercent === "number" ? <div key={`${index}:${kind}`} className="wb-limit"><span>{kind === "primary" ? "Session window" : "Longer window"}</span><strong>{Math.max(0, 100 - window.usedPercent).toFixed(0)}% left</strong><progress max={100} value={Math.max(0, 100 - window.usedPercent)} />{window.resetsAt ? <small>Resets {new Date(window.resetsAt * 1000).toLocaleString()}</small> : null}</div> : null; }))}
        {!windows.length ? <p className="wb-muted">{row.unavailable || "Account limits were not reported by this runtime."}</p> : null}
        {row.usage && Object.keys(row.usage).length ? <><p className="wb-muted">{row.scope}</p><dl>{Object.entries(row.usage).map(([key,value])=><React.Fragment key={key}><dt>{key.replace(/_/g," ")}</dt><dd>{String(value)}</dd></React.Fragment>)}</dl></> : null}
        {typeof row.reported_cost_usd === "number" ? <p>Runtime-reported cost: ${row.reported_cost_usd.toFixed(4)} <small>(not a subscription bill)</small></p> : null}
        {row.observed_at ? <small>Observed {new Date(row.observed_at*1000).toLocaleString()}</small> : null}
      </section>;
    })}{data && !data.runtimes.length ? <p className="wb-muted">Connect a coding runtime to see the usage it reports.</p> : null}<p className="wb-footnote">{data?.note}</p>
    {data?.diagnostics?.resources ? <section><strong>This computer</strong><p className="wb-muted">A current host observation; no automatic routing.</p><dl>{Object.entries(data.diagnostics.resources).map(([key,value])=><React.Fragment key={key}><dt>{key.replace(/_/g," ")}</dt><dd>{typeof value === "number" ? key.endsWith("bytes") ? `${(value/1024/1024).toFixed(0)} MiB` : `${value.toFixed(1)}%` : String(value)}</dd></React.Fragment>)}</dl></section> : null}</div>;
}

function AgentActivity({ apiBase, research }: { apiBase: string; research: boolean }) {
  const context = useWorkbench(state => state.context);
  const [data, setData] = useState<{ runtime: string; items: { id: string; name: string; status: string; research: boolean; details: unknown }[] } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true; setData(null);
    const update = async () => { try { const value = await request<any>(workbenchUrl(apiBase, context, "activity")); if (live) setData(value); } catch (err) { if (live) setError(err instanceof Error ? err.message : "Activity unavailable"); } };
    void update(); const timer = setInterval(update, 1000); return () => { live = false; clearInterval(timer); };
  }, [apiBase, context.project_id, context.external_session_id]);
  const rows = data?.items.filter(item => !research || item.research) || [];
  return <div className="wb-agent-activity"><header><RuntimeMark runtime={data?.runtime || "echo"} /><strong>{research ? "Agent research" : "Agent activity"}</strong></header>
    {error ? <p className="wb-error" role="alert">{error}</p> : null}{rows.length ? <ol>{rows.map(row => <li key={row.id}><details><summary><span>{row.name}</span><small>{row.status}</small></summary><pre>{JSON.stringify(row.details, null, 2)}</pre></details></li>)}</ol> : <p className="wb-muted">{research ? "Search and fetch tools appear when this agent reports them." : "Tools and file changes reported by this agent appear here."}</p>}
    <small className="wb-footnote">Activity belongs to the selected native agent session. Earlier events may leave its bounded replay window.</small></div>;
}

export function WorkbenchContent({ apiBase, nativeAllowed }: { apiBase: string; nativeAllowed: boolean }) {
  const store = useWorkbench();
  const active = store.tabs.find(tab => tab.id === store.active);
  const { data: summary } = useResource<Summary>(apiBase, "summary");
  if (!active || active.kind === "surfaces") return <SurfacePicker nativeAllowed={nativeAllowed} />;
  if (!store.context.project_id && !["browser", "research", "activity"].includes(active.kind)) return <div className="wb-empty"><strong>Choose a project</strong><p>Attach or select a folder to open this workspace surface.</p><button className="es-btn" onClick={() => window.dispatchEvent(new CustomEvent("echospeak:workbench-add-folder"))}>Add project folder</button></div>;
  switch (active.kind) {
    case "files": return <Files apiBase={apiBase} />;
    case "file": return <FileView key={active.path} apiBase={apiBase} path={active.path || ""} />;
    case "diff": return <Changes key={contextKey(store.context)} apiBase={apiBase} />;
    case "terminal": return <TerminalDock apiBase={apiBase} side />;
    case "browser": return <Browser apiBase={apiBase} />;
    case "pull_requests": return <PullRequests apiBase={apiBase} linked={false} />;
    case "linked_prs": return <PullRequests apiBase={apiBase} linked />;
    case "usage": return <Usage apiBase={apiBase} />;
    case "activity": case "research": return store.context.external_session_id ? <AgentActivity apiBase={apiBase} research={active.kind === "research"} /> : <div className="wb-empty">Open a conversation to view its research and activity.</div>;
    case "device": return <div className="wb-device"><WorkbenchIcon kind="device" /><strong>{summary?.computer || "This computer"}</strong><p>{summary?.system}</p><p>{summary?.name}</p><small>Current local workspace host. Screen observations remain in Echo's permission-controlled Screen surface.</small></div>;
    default: return nativeAllowed ? null : <div className="wb-empty">Open a conversation to view its research and activity.</div>;
  }
}
