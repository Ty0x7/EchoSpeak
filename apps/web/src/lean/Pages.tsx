import React, { useEffect, useState } from "react";
import { RoutinesGroup } from "../settings/SettingsPanel";
import { AvatarStack } from "./Roster";
import type { LeanPersona, LeanRoom } from "./types";

/** Pages opened from the sidebar nav (Group chats, Projects, Artifacts, Routines). */

export type ArtifactSummary = {
  id: string;
  title: string;
  kind: string;
  version: number;
  versions: number;
  session_id: string;
  agent_id?: string;
  updated_at: number;
};

type Project = { id: string; name: string; workspace_root?: string; archived?: boolean };

function PageShell({ title, lead, action, children }: { title: string; lead: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="es-page" aria-label={title}>
      <header className="es-page-head">
        <div>
          <h1>{title}</h1>
          <p>{lead}</p>
        </div>
        {action}
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
        <div className="es-page-list">
          {groups.map((room) => {
            const members = room.agent_ids.map((id) => byId.get(id)).filter(Boolean) as LeanPersona[];
            return (
              <div key={room.id} className={`es-page-row${room.thread_id === activeThreadId ? " is-active" : ""}`}>
                <button type="button" className="es-page-row-main" onClick={() => onOpen(room)}>
                  <AvatarStack agents={members} size={22} max={4} />
                  <span className="es-page-row-text">
                    <strong>{room.name}{room.mode === "discussion" ? <span className="es-room-mode">Discussion</span> : null}</strong>
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
      )}
    </PageShell>
  );
}

export function ProjectsPage({
  projects,
  chatCounts,
  activeProjectId,
  onOpen,
  onAdd,
}: {
  projects: Project[];
  chatCounts: Record<string, number>;
  activeProjectId: string;
  onOpen(project: Project): void;
  onAdd(): void;
}) {
  const shown = projects.filter((p) => !p.archived);
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
        <div className="es-page-grid">
          {shown.map((project) => (
            <button key={project.id} type="button" className={`es-page-card${project.id === activeProjectId ? " is-active" : ""}`} onClick={() => onOpen(project)}>
              <strong>{project.name}</strong>
              <small className="is-mono">{project.workspace_root || "No folder"}</small>
              <span>{chatCounts[project.id] ? `${chatCounts[project.id]} chat${chatCounts[project.id] === 1 ? "" : "s"}` : "No chats yet"}</span>
            </button>
          ))}
        </div>
      )}
    </PageShell>
  );
}

const KIND_LABEL: Record<string, string> = { html: "App", svg: "SVG", mermaid: "Diagram", markdown: "Document", code: "Code" };

export function ArtifactsPage({ apiBase, onOpen }: { apiBase: string; onOpen(item: ArtifactSummary): void }) {
  const [items, setItems] = useState<ArtifactSummary[] | null>(null);
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
        <div className="es-page-grid">
          {items.map((item) => (
            <button key={item.id} type="button" className="es-page-card" onClick={() => onOpen(item)}>
              <span className="es-art-kind">{KIND_LABEL[item.kind] || item.kind}</span>
              <strong>{item.title}</strong>
              <span>{item.versions > 1 ? `v${item.version} of ${item.versions} · ` : ""}{ago(item.updated_at * 1000)}</span>
            </button>
          ))}
        </div>
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
