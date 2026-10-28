import React, { useCallback, useEffect, useState } from "react";
import { RuntimeMark } from "../components/RuntimeMark";
import { request } from "../projects/model";
import { runtimeName, type CodeSession } from "./model";

/** An external runtime's session: its own store and worktree, never an Echo thread. */
export type ExternalRow = { id: string; runtime_id: string; state: string; created_at: number; project_id: string };
export type CodeProjectRow = { id: string; name: string; workspace_root: string; trust_state: string; sessions: CodeSession[]; external?: ExternalRow[] };
export type CodeOverview = { projects: CodeProjectRow[]; unorganized: CodeSession[] };

const DRAG_TYPE = "application/x-echospeak-code-session";
const FOLDED_KEY = "echospeak.code.folded";

function readFolded(): string[] {
  try { return JSON.parse(window.localStorage.getItem(FOLDED_KEY) || "[]"); } catch { return []; }
}

/** Only Echo's own sessions move between projects; external ones live in their project's worktree. */
export function canDropInto(project: Pick<CodeProjectRow, "id" | "trust_state">, fromProjectId: string): boolean {
  return project.trust_state === "trusted" && project.id !== fromProjectId;
}

export function externalTitle(row: ExternalRow): string {
  const when = new Date(row.created_at * 1000);
  const date = Number.isNaN(when.getTime()) ? "" : when.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${runtimeName(row.runtime_id)}${date ? ` · ${date}` : ""}${row.state === "rolled_back" ? " · rolled back" : ""}`;
}

/**
 * Code's sidebar: projects, each with its coding sessions, and the sessions whose project is gone.
 * Drag an Echo session onto a project to move it there (or use Move to…).
 */
export function CodeTree({ apiBase, revision, activeThreadId, activeExternalId, onOpenNative, onOpenExternal, onNewSession, onAddFolder }: {
  apiBase: string; revision: number; activeThreadId: string; activeExternalId: string;
  onOpenNative(session: CodeSession): void; onOpenExternal(projectId: string, runtimeId: string, sessionId: string): void;
  onNewSession(projectId: string): void; onAddFolder(): void;
}) {
  const [data, setData] = useState<CodeOverview | null>(null);
  const [error, setError] = useState("");
  const [folded, setFolded] = useState<string[]>(readFolded);
  const [dropTarget, setDropTarget] = useState("");
  const [moving, setMoving] = useState("");
  const [localRevision, setLocalRevision] = useState(0);
  useEffect(() => {
    try { window.localStorage.setItem(FOLDED_KEY, JSON.stringify(folded)); } catch { /* storage unavailable */ }
  }, [folded]);
  useEffect(() => {
    let live = true;
    request<CodeOverview>(`${apiBase}/echo-code/overview`)
      .then(value => { if (live) { setData(value); setError(""); } })
      .catch(err => { if (live) setError(err instanceof Error ? err.message : "Couldn't load projects"); });
    return () => { live = false; };
  }, [apiBase, revision, localRevision]);

  const move = useCallback(async (threadId: string, projectId: string) => {
    setMoving(""); setDropTarget("");
    try {
      await request(`${apiBase}/echo-code/sessions/${encodeURIComponent(threadId)}/project`, { project_id: projectId });
      setLocalRevision(value => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't move the session");
    }
  }, [apiBase]);

  const projects = data?.projects || [];
  const projectOf = (threadId: string) => projects.find(project => project.sessions.some(row => row.thread_id === threadId))?.id || "";
  const nativeRow = (session: CodeSession) => {
    const active = session.thread_id === activeThreadId;
    const from = projectOf(session.thread_id);
    const targets = projects.filter(project => canDropInto(project, from));
    return <li key={session.thread_id} className={`ec-tree-row${active ? " is-active" : ""}`} draggable
      onDragStart={event => { event.dataTransfer.setData(DRAG_TYPE, session.thread_id); event.dataTransfer.setData("text/plain", session.title); event.dataTransfer.effectAllowed = "move"; }}>
      <button type="button" className="ec-tree-open" aria-current={active ? "true" : undefined} title={session.title} onClick={() => onOpenNative(session)}>
        <RuntimeMark runtime="echo" /><span>{session.title.replace(/^Code · /, "") || "Coding session"}</span>
      </button>
      {targets.length ? (moving === session.thread_id
        ? <select autoFocus aria-label={`Move ${session.title} to project`} defaultValue="" onBlur={() => setMoving("")}
            onChange={event => { if (event.target.value) void move(session.thread_id, event.target.value); }}>
            <option value="" disabled>Move to…</option>
            {targets.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
        : <button type="button" className="ec-tree-action" title="Move to another project" aria-label={`Move ${session.title}`} onClick={() => setMoving(session.thread_id)}>⇄</button>) : null}
    </li>;
  };
  const dropProps = (project: CodeProjectRow) => ({
    onDragOver: (event: React.DragEvent) => {
      if (!event.dataTransfer.types.includes(DRAG_TYPE) || project.trust_state !== "trusted") return;
      event.preventDefault(); event.dataTransfer.dropEffect = "move"; setDropTarget(project.id);
    },
    onDragLeave: () => setDropTarget(current => current === project.id ? "" : current),
    onDrop: (event: React.DragEvent) => {
      const threadId = event.dataTransfer.getData(DRAG_TYPE);
      event.preventDefault(); setDropTarget("");
      if (threadId && canDropInto(project, projectOf(threadId))) void move(threadId, project.id);
    },
  });

  return <div className="ec-tree" aria-label="Projects and coding sessions">
    <div className="es-sec-head ec-tree-head">
      <span className="es-sec-title">Projects</span>
      <span className="es-sec-count">{projects.length}</span>
      <button className="es-sec-action" type="button" onClick={onAddFolder} title="Add a project folder" aria-label="Add a project folder">+</button>
    </div>
    {error ? <p role="alert" className="ec-tree-note">{error}</p> : null}
    {!data && !error ? <p role="status" className="ec-tree-note">Loading projects…</p> : null}
    {data && !projects.length ? <p className="ec-tree-note">Add a project folder to start coding.</p> : null}
    <ul className="ec-tree-projects">
      {projects.map(project => {
        const open = !folded.includes(project.id);
        const external = (project.external || []).slice().sort((a, b) => b.created_at - a.created_at);
        const count = project.sessions.length + external.length;
        return <li key={project.id} className={`ec-tree-project${dropTarget === project.id ? " is-drop" : ""}`} {...dropProps(project)}>
          <div className="ec-tree-project-head">
            <button type="button" className="ec-tree-toggle" aria-expanded={open} title={project.workspace_root}
              onClick={() => setFolded(value => open ? [...value, project.id] : value.filter(id => id !== project.id))}>
              <span aria-hidden className="ec-tree-chev">{open ? "▾" : "▸"}</span><span>{project.name}</span><small>{count || ""}</small>
            </button>
            <button type="button" className="ec-tree-action" title={`New coding session in ${project.name}`} aria-label={`New coding session in ${project.name}`} onClick={() => onNewSession(project.id)}>+</button>
          </div>
          {open ? <ul>
            {project.sessions.map(nativeRow)}
            {external.map(row => {
              const active = row.id === activeExternalId;
              return <li key={row.id} className={`ec-tree-row${active ? " is-active" : ""}`}>
                <button type="button" className="ec-tree-open" aria-current={active ? "true" : undefined} title={`${externalTitle(row)} · stays in this project's worktree`}
                  onClick={() => onOpenExternal(project.id, row.runtime_id, row.id)}>
                  <RuntimeMark runtime={row.runtime_id} /><span>{externalTitle(row)}</span>
                </button>
              </li>;
            })}
            {!count ? <li className="ec-tree-empty">No sessions yet</li> : null}
          </ul> : null}
        </li>;
      })}
    </ul>
    {data?.unorganized.length ? <section className="ec-tree-unorganized" aria-label="Unorganized sessions">
      <div className="es-sec-head ec-tree-head"><span className="es-sec-title">Unorganized</span><span className="es-sec-count">{data.unorganized.length}</span></div>
      <p className="ec-tree-note">Their project was removed. Drag one onto a project to keep working there.</p>
      <ul>{data.unorganized.map(nativeRow)}</ul>
    </section> : null}
  </div>;
}
