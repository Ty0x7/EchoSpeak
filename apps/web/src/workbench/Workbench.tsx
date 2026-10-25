import React, { useEffect, useRef, useState } from "react";
import { WorkspacePopover } from "../components/WorkspacePopover";
import { RuntimeMark } from "../components/RuntimeMark";
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
    {store.detailsOpen ? <WorkspacePopover anchor={anchor} label="Workspace details" width={340} onClose={() => useWorkbench.setState({ detailsOpen: false })}>
      <WorkspaceDetails apiBase={apiBase} />
    </WorkspacePopover> : null}
  </div>;
}

export function WorkspaceDetails({ apiBase }: { apiBase: string }) {
  const store = useWorkbench();
  const { data, error } = useResource<Summary>(apiBase, "summary");
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [notice, setNotice] = useState("");
  const action = async (work: () => Promise<unknown>) => { setNotice(""); try { await work(); } catch (err) { setNotice(err instanceof Error ? err.message : "Workspace action failed"); } };
  return <div className="wb-details">
    <strong className="wb-detail-heading"><WorkbenchIcon kind="device" />{data?.computer || "This computer"}</strong>
    <span className="wb-detail-context">{data?.name || "Choose a project"}{store.context.external_session_id ? " · Agent worktree" : ""}</span>
    {!store.context.project_id ? <p>Choose a project folder to open files, Git changes and a terminal.</p> : null}
    <button onClick={() => void action(() => request(`${apiBase}/workbench/editor`, store.context))} disabled={!data}><WorkbenchIcon kind="file" />Open in VS Code</button>
    <button onClick={() => window.dispatchEvent(new CustomEvent("echospeak:workbench-add-folder"))}><span aria-hidden>＋</span>Add project folder</button>
    <button disabled={!data} onClick={() => setEditing(value => !value)}><span aria-hidden>＋</span>Add project script</button>
    {editing ? <form className="wb-script-form" onSubmit={event => { event.preventDefault(); void action(async () => {
      await request(`${apiBase}/workbench/scripts`, { ...store.context, name, command }); setEditing(false); setName(""); setCommand(""); store.refresh();
    }); }}><label>Script name<input required value={name} onChange={event => setName(event.target.value)} maxLength={80} /></label>
      <label>Command<input required value={command} onChange={event => setCommand(event.target.value)} placeholder="npm run dev" maxLength={2000} /></label>
      <button type="submit" className="es-btn">Save script</button><small>Saving does not run the script.</small></form> : null}
    {data?.scripts.map(script => <button key={script.id} title={script.command} onClick={() => void action(async () => {
      const terminal = await request<{ id: string }>(`${apiBase}/workbench/scripts/${script.id}/run`, store.context); store.addTerminal(terminal.id, store.context); useWorkbench.setState({ bottomOpen: true });
    })}><WorkbenchIcon kind="terminal" />Run {script.name}</button>)}
    <hr />
    <button disabled={!data?.git.is_git} onClick={() => store.open("diff")}><WorkbenchIcon kind="pull_requests" /><span>{data?.git.branch === "HEAD" ? "Detached agent worktree" : data?.git.branch || "No Git repository"}</span></button>
    {data?.git.head ? <small className="wb-commit-id">{data.git.head.slice(0, 10)} · {data.git.repository || "Local repository"}</small> : null}
    <button disabled={!data?.git.is_git} onClick={() => store.open("pull_requests")}><WorkbenchIcon kind="pull_requests" />Pull requests</button>
    <button disabled={!data?.git.is_git} onClick={() => store.open("diff")}><WorkbenchIcon kind="diff" />Commit &amp; push<span className="wb-row-end">Review</span></button>
    <button disabled={!data?.git.is_git} onClick={() => store.open("diff")}><WorkbenchIcon kind="files" />Changes<span className="wb-row-end">{data?.git.changes.length || 0}</span></button>
    <hr /><div className="wb-runtime-line"><RuntimeMark runtime={data?.runtime || "echo"} />{data?.runtime === "claude_code" ? "Claude Agent" : data?.runtime === "codex" ? "Codex" : data?.runtime === "acp" ? "ACP agent" : "Echo native"}</div>
    <button onClick={() => store.open("usage")}><WorkbenchIcon kind="usage" />Usage &amp; limits</button>
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

function Usage({ apiBase }: { apiBase: string }) {
  const { data, error } = useResource<{ runtimes: { runtime: string; session_id: string; limits?: any; usage?: Record<string, unknown>; unavailable?: string }[]; note: string }>(apiBase, "usage");
  return <div className="wb-usage"><header><strong>Usage &amp; limits</strong><button aria-label="Refresh usage" onClick={useWorkbench.getState().refresh}>↻</button></header>
    {error ? <p role="alert" className="wb-error">{error}</p> : null}{data?.runtimes.map(row => {
      const windows = row.limits?.rateLimitsByLimitId ? Object.values(row.limits.rateLimitsByLimitId) as any[] : row.limits?.rateLimits ? [row.limits.rateLimits] : [];
      return <section key={row.session_id}><div className="wb-runtime-line"><RuntimeMark runtime={row.runtime} /><strong>{row.runtime === "claude_code" ? "Claude Agent" : row.runtime === "codex" ? "Codex" : "ACP agent"}</strong><small>{row.session_id.slice(0, 8)}</small></div>
        {windows.flatMap((limit, index) => ["primary", "secondary"].map(kind => { const window = limit[kind]; return window && typeof window.usedPercent === "number" ? <div key={`${index}:${kind}`} className="wb-limit"><span>{kind === "primary" ? "Session window" : "Longer window"}</span><strong>{Math.max(0, 100 - window.usedPercent).toFixed(0)}% left</strong><progress max={100} value={Math.max(0, 100 - window.usedPercent)} />{window.resetsAt ? <small>Resets {new Date(window.resetsAt * 1000).toLocaleString()}</small> : null}</div> : null; }))}
        {!windows.length ? <p className="wb-muted">{row.unavailable || "Account limits were not reported by this runtime."}</p> : null}
        {row.usage && Object.keys(row.usage).length ? <details><summary>Reported API usage</summary><pre>{JSON.stringify(row.usage, null, 2)}</pre></details> : null}
      </section>;
    })}{data && !data.runtimes.length ? <p className="wb-muted">Connect a coding runtime to see the usage it reports.</p> : null}<p className="wb-footnote">{data?.note}</p></div>;
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
