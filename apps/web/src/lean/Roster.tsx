import React from "react";
import { AgentAvatar } from "./LeanMessage";
import type { LeanPersona, LeanRoom } from "./types";

export function AvatarStack({ agents, size = 20, max = 4 }: { agents: LeanPersona[]; size?: number; max?: number }) {
  const shown = agents.slice(0, max);
  return (
    <span className="es-stack" aria-hidden>
      {shown.map((agent) => (
        <AgentAvatar key={agent.id} id={agent.id} name={agent.name} initials={agent.initials} size={size} />
      ))}
      {agents.length > max ? <span className="lm-avatar es-stack-more" style={{ width: size, height: size }}>+{agents.length - max}</span> : null}
    </span>
  );
}

/** Agents and group chats, rendered inside the sidebar scroll area. */
export function RosterSections({
  agents,
  rooms,
  activeThreadId,
  collapsed,
  onOpenAgent,
  onEditAgent,
  onNewAgent,
  onOpenRoom,
  onNewRoom,
  onDeleteRoom,
}: {
  agents: LeanPersona[];
  rooms: LeanRoom[];
  activeThreadId: string;
  collapsed: boolean;
  onOpenAgent(agent: LeanPersona): void;
  onEditAgent(agent: LeanPersona): void;
  onNewAgent(): void;
  onOpenRoom(room: LeanRoom): void;
  onNewRoom(): void;
  onDeleteRoom(room: LeanRoom): void;
}) {
  const byId = new Map(agents.map((a) => [a.id, a]));
  const groups = rooms.filter((r) => r.kind === "group");
  if (collapsed) {
    return (
      <section className="es-roster is-collapsed" aria-label="Agents">
        {agents.slice(0, 6).map((agent) => (
          <button key={agent.id} type="button" className="es-roster-icon" title={`Chat with ${agent.name}`} onClick={() => onOpenAgent(agent)}>
            <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={24} />
          </button>
        ))}
      </section>
    );
  }
  return (
    <>
      <section className="es-roster" aria-label="Agents">
        <div className="es-section-head">
          <span>Agents</span>
          <button type="button" className="es-icon-btn" title="New agent" aria-label="New agent" onClick={onNewAgent}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 6v12M6 12h12" /></svg>
          </button>
        </div>
        <div className="es-agent-grid">
          {agents.map((agent) => {
            const direct = rooms.find((r) => r.kind === "direct" && r.agent_ids.length === 1 && r.agent_ids[0] === agent.id);
            const active = Boolean(direct && direct.thread_id === activeThreadId);
            return (
              <div key={agent.id} className={`es-agent-chip${active ? " is-active" : ""}`}>
                <button type="button" className="es-agent-main" onClick={() => onOpenAgent(agent)} title={agent.description || agent.title}>
                  <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={26} />
                  <span className="es-agent-text">
                    <strong>{agent.name}</strong>
                    <small>{agent.title || "Agent"}</small>
                  </span>
                </button>
                <button type="button" className="es-icon-btn es-agent-edit" title={`Edit ${agent.name}`} aria-label={`Edit ${agent.name}`} onClick={() => onEditAgent(agent)}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" /></svg>
                </button>
              </div>
            );
          })}
        </div>
      </section>
      <section className="es-roster" aria-label="Group chats">
        <div className="es-section-head">
          <span>Group chats</span>
          <button type="button" className="es-icon-btn" title="New group chat" aria-label="New group chat" onClick={onNewRoom}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 6v12M6 12h12" /></svg>
          </button>
        </div>
        {groups.length === 0 ? (
          <button type="button" className="es-empty-row" onClick={onNewRoom}>Start a group chat with your agents</button>
        ) : (
          <div className="es-room-list">
            {groups.map((room) => {
              const members = room.agent_ids.map((id) => byId.get(id)).filter(Boolean) as LeanPersona[];
              const active = room.thread_id === activeThreadId;
              return (
                <div key={room.id} className={`es-room-row${active ? " is-active" : ""}`}>
                  <button type="button" className="es-room-main" onClick={() => onOpenRoom(room)}>
                    <AvatarStack agents={members} size={18} max={3} />
                    <span className="es-room-text">
                      <strong>{room.name}</strong>
                      <small>{room.last_preview || members.map((m) => m.name).join(", ")}</small>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="es-icon-btn es-room-delete"
                    title="Delete group chat"
                    aria-label={`Delete ${room.name}`}
                    onClick={() => {
                      if (window.confirm(`Delete “${room.name}” and its messages?`)) onDeleteRoom(room);
                    }}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M5.5 7.5h13M9 7.5V5.7h6v1.8M7.5 7.5l.7 11h7.6l.7-11" /></svg>
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </>
  );
}
