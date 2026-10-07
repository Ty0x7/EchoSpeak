import React, { createContext, useContext, useEffect, useState } from "react";
import { RoutinesGroup } from "../settings/SettingsPanel";
import { ArtifactKindIcon, ARTIFACT_KIND_LABEL } from "../widgets/ArtifactPanel";
import { AvatarStack } from "./Roster";
import { ShowMore, useShowMore } from "./ShowMore";
import type { LeanPersona, LeanRoom } from "./types";

/** Pages opened from the sidebar nav (Group chats, Projects, Artifacts, Routines). */

export type ArtifactSummary = {
  id: string;
  title: string;
  kind: string;
  language?: string;
  version: number;
  versions: number;
  session_id: string;
  agent_id?: string;
  created_at?: number;
  updated_at: number;
  /** A one-line glimpse of the content (empty for images). */
  excerpt?: string;
  lines?: number;
};

type Project = { id: string; name: string; workspace_root?: string; archived?: boolean };

/** Closes the open page and brings the chat back. Provided by the app shell. */
export const PageCloseContext = createContext<(() => void) | null>(null);

export function PageShell({ title, lead, action, children }: { title: string; lead: string; action?: React.ReactNode; children: React.ReactNode }) {
  const close = useContext(PageCloseContext);
  useEffect(() => {
    if (!close) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);
  return (
    <section className="es-page" aria-label={title}>
      <header className="es-page-head">
        <div>
          <h1>{title}</h1>
          <p>{lead}</p>
        </div>
        <div className="es-page-actions">
          {action}
          {close ? (
            <button type="button" className="es-page-close" onClick={close} title="Back to chat (Esc)" aria-label="Close and go back to the chat">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden><path d="M6 6l12 12M18 6 6 18" /></svg>
            </button>
          ) : null}
        </div>
      </header>
      <div className="es-page-body">{children}</div>
    </section>
  );
}

const ago = (ms: number) => {
  if (!ms) return "";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ms).toLocaleDateString([], { month: "short", day: "numeric" });
};

export function GroupChatsPage({
  agents,
  rooms,
  activeThreadId,
  onOpen,
  onNew,
  onDelete,
}: {
  agents: LeanPersona[];
  rooms: LeanRoom[];
  activeThreadId: string;
  onOpen(room: LeanRoom): void;
  onNew(): void;
  onDelete(room: LeanRoom): void;
}) {
  const byId = new Map(agents.map((a) => [a.id, a]));
  const groups = rooms.filter((r) => r.kind === "group").sort((a, b) => (b.last_message_at || b.updated_at || 0) - (a.last_message_at || a.updated_at || 0));
  const more = useShowMore(groups, groups.findIndex((room) => room.thread_id === activeThreadId));
  return (
    <PageShell
      title="Group chats"
      lead="Chats with several agents. They hand work to each other and finish with a Done or Stopped line."
      action={<button type="button" className="es-btn es-btn-primary" onClick={onNew}>New group chat</button>}
    >
      {groups.length === 0 ? (
        <button type="button" className="es-page-empty" onClick={onNew}>
          <strong>No group chats yet</strong>
          <span>Pick two or more agents, for example Jarvis to research and Glados to build.</span>
        </button>
      ) : (
        <>
        <div className="es-page-list">
          {more.shown.map((room) => {
            const members = room.agent_ids.map((id) => byId.get(id)).filter(Boolean) as LeanPersona[];
            return (
              <div key={room.id} className={`es-page-row${room.thread_id === activeThreadId ? " is-active" : ""}`}>
                <button type="button" className="es-page-row-main" onClick={() => onOpen(room)}>
                  <AvatarStack agents={members} size={22} max={4} />
                  <span className="es-page-row-text">
                    <strong>{room.name}{room.mode === "discussion" ? <span className="es-room-mode">Work together</span> : null}</strong>
                    <small>{room.last_preview || members.map((m) => m.name).join(", ")}</small>
                  </span>
                  <time>{ago(Math.max(Number(room.last_message_at || 0), Number(room.updated_at || 0)) * 1000)}</time>
                </button>
                <button
                  type="button"
                  className="es-icon-btn"
                  title="Delete group chat"
                  aria-label={`Delete ${room.name}`}
                  onClick={() => {
                    if (window.confirm(`Delete “${room.name}” and its messages?`)) onDelete(room);
                  }}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M5.5 7.5h13M9 7.5V5.7h6v1.8M7.5 7.5l.7 11h7.6l.7-11" /></svg>
                </button>
              </div>
            );
          })}
        </div>
        {more.collapsible ? <ShowMore expanded={more.expanded} hidden={more.hidden} label="group chats" onToggle={() => more.setExpanded((v) => !v)} /> : null}
        </>
      )}
    </PageShell>
  );
}

const TRASH = <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M5.5 7.5h13M9 7.5V5.7h6v1.8M7.5 7.5l.7 11h7.6l.7-11" /></svg>;

export function ProjectsPage({
  projects,
  chatCounts,
  activeProjectId,
  onOpen,
  onAdd,
  onNewChat,
  onDelete,
}: {
  projects: Project[];
  chatCounts: Record<string, number>;
  activeProjectId: string;
  onOpen(project: Project): void;
  onAdd(): void;
  onNewChat(project: Project): void;
  onDelete(project: Project): void;
}) {
  const shown = projects.filter((p) => !p.archived);
  const more = useShowMore(shown, shown.findIndex((project) => project.id === activeProjectId));
  return (
    <PageShell
      title="Projects"
      lead="Local folders your agents can read, edit and run code in."
      action={<button type="button" className="es-btn es-btn-primary" onClick={onAdd}>Add folder</button>}
    >
      {shown.length === 0 ? (
        <button type="button" className="es-page-empty" onClick={onAdd}>
          <strong>No projects yet</strong>
          <span>Attach a folder and chats inside it work on those files.</span>
        </button>
      ) : (
        <>
        <div className="es-page-list">
          {more.shown.map((project) => {
            const chats = chatCounts[project.id] || 0;
            return (
              <div key={project.id} className={`es-page-row${project.id === activeProjectId ? " is-active" : ""}`}>
                <button type="button" className="es-page-row-main" onClick={() => onOpen(project)}>
                  <span className="es-row-tile is-folder" aria-hidden>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.2l2 2h8.8A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" /></svg>
                  </span>
                  <span className="es-page-row-text">
                    <strong>{project.name}</strong>
                    <small className="is-mono">{project.workspace_root || "No folder"}</small>
                  </span>
                  <span className="es-row-meta">{chats ? `${chats} chat${chats === 1 ? "" : "s"}` : "No chats yet"}</span>
                </button>
                <button type="button" className="es-icon-btn" title="New chat in this project" aria-label={`New chat in ${project.name}`} onClick={() => onNewChat(project)}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
                </button>
                <button type="button" className="es-icon-btn" title="Remove project" aria-label={`Remove ${project.name}`} onClick={() => {
                  if (window.confirm(`Remove the project “${project.name}”? Its chats are kept, and the folder on disk is not touched.`)) onDelete(project);
                }}>{TRASH}</button>
              </div>
            );
          })}
        </div>
        {more.collapsible ? <ShowMore expanded={more.expanded} hidden={more.hidden} label="projects" onToggle={() => more.setExpanded((v) => !v)} /> : null}
        </>
      )}
    </PageShell>
  );
}

/** Artifact kinds as the library shows them, in filter order. */
const KINDS: { id: string; label: string }[] = [
  { id: "html", label: "Apps" },
  { id: "markdown", label: "Documents" },
  { id: "code", label: "Code" },
  { id: "mermaid", label: "Diagrams" },
  { id: "svg", label: "Images" },
];
const KIND_ONE = ARTIFACT_KIND_LABEL;

/** Today, this week, earlier: how the library groups artifacts. */
function whenGroup(seconds: number): string {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  if (seconds >= startOfToday) return "Today";
  if (seconds >= startOfToday - 6 * 86400) return "Previous 7 days";
  return "Earlier";
}

export function ArtifactsPage({ apiBase, onOpen }: { apiBase: string; onOpen(item: ArtifactSummary): void }) {
  const [items, setItems] = useState<ArtifactSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  useEffect(() => {
    let live = true;
    fetch(`${apiBase}/lean/artifacts`)
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((data) => live && setItems(Array.isArray(data.items) ? data.items : []))
      .catch(() => live && setItems([]));
    return () => {
      live = false;
    };
  }, [apiBase]);
  const all = (items || []).slice().sort((a, b) => b.updated_at - a.updated_at);
  const counts = all.reduce<Record<string, number>>((acc, item) => ((acc[item.kind] = (acc[item.kind] || 0) + 1), acc), {});
  const needle = query.trim().toLowerCase();
  const filtered = all.filter((item) => (!kind || item.kind === kind)
    && (!needle || `${item.title} ${item.excerpt || ""}`.toLowerCase().includes(needle)));
  const more = useShowMore(filtered, -1, 12);
  const remove = async (item: ArtifactSummary) => {
    if (!window.confirm(`Delete “${item.title}” and all of its versions?`)) return;
    const response = await fetch(`${apiBase}/lean/artifacts/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    if (response.ok) setItems((current) => (current || []).filter((row) => row.id !== item.id));
  };
  const groups: { label: string; rows: ArtifactSummary[] }[] = [];
  for (const item of more.shown) {
    const label = whenGroup(item.updated_at);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.rows.push(item);
    else groups.push({ label, rows: [item] });
  }
  return (
    <PageShell title="Artifacts" lead="Apps, documents, diagrams and code your agents made. Open one to keep working on it.">
      {items === null ? (
        <div className="es-sec-empty" role="status">Loading…</div>
      ) : items.length === 0 ? (
        <div className="es-page-empty is-static">
          <strong>No artifacts yet</strong>
          <span>Ask for something you can use or keep, like “build me a tip calculator”.</span>
        </div>
      ) : (
        <>
        <div className="es-page-toolbar">
          <label className="es-page-search">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search artifacts" aria-label="Search artifacts" />
          </label>
          <div className="es-page-chips" role="group" aria-label="Type">
            <button type="button" aria-pressed={!kind} onClick={() => setKind("")}>All <small>{all.length}</small></button>
            {KINDS.filter((k) => counts[k.id]).map((k) => (
              <button key={k.id} type="button" aria-pressed={kind === k.id} onClick={() => setKind(kind === k.id ? "" : k.id)}>{k.label} <small>{counts[k.id]}</small></button>
            ))}
          </div>
        </div>
        {filtered.length === 0 ? (
          <div className="es-sec-empty">No artifacts match.</div>
        ) : groups.map((group) => (
          <section key={group.label} className="es-page-group" aria-label={group.label}>
            <h2>{group.label}</h2>
            <div className="es-page-list">
              {group.rows.map((item) => (
                <div key={item.id} className="es-page-row es-art-row">
                  <button type="button" className="es-page-row-main" onClick={() => onOpen(item)}>
                    <span className={`es-row-tile is-${item.kind}`} aria-hidden><ArtifactKindIcon kind={item.kind} /></span>
                    <span className="es-page-row-text">
                      <strong>{item.title}</strong>
                      <small>{item.excerpt || [item.kind === "code" && item.language ? item.language.charAt(0).toUpperCase() + item.language.slice(1) : KIND_ONE[item.kind] || item.kind, item.lines ? `${item.lines} line${item.lines === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ")}</small>
                    </span>
                    <span className="es-row-meta">
                      <span>{KIND_ONE[item.kind] || item.kind}{item.versions > 1 ? ` · v${item.version}` : ""}</span>
                      <time>{ago(item.updated_at * 1000)}</time>
                    </span>
                  </button>
                  <button type="button" className="es-icon-btn" title="Delete artifact" aria-label={`Delete ${item.title}`} onClick={() => void remove(item)}>{TRASH}</button>
                </div>
              ))}
            </div>
          </section>
        ))}
        {more.collapsible ? <ShowMore expanded={more.expanded} hidden={more.hidden} label="artifacts" onToggle={() => more.setExpanded((v) => !v)} /> : null}
        </>
      )}
    </PageShell>
  );
}

export function RoutinesPage({ apiBase, agents }: { apiBase: string; agents: LeanPersona[] }) {
  return (
    <PageShell title="Routines" lead="Tasks your agents run on a schedule or when you press Run. Results land in their own chat.">
      <div className="es-settings-embed">
        <RoutinesGroup apiBase={apiBase} agents={agents} embedded />
      </div>
    </PageShell>
  );
}
