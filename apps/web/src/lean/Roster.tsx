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

/** Compact agent rows for the sidebar's Agents section: face + name, details on hover. */
export function AgentRows({
  agents,
  rooms,
  activeThreadId,
  onOpenAgent,
  onEditAgent,
}: {
  agents: LeanPersona[];
  rooms: LeanRoom[];
  activeThreadId: string;
  onOpenAgent(agent: LeanPersona): void;
  onEditAgent(agent: LeanPersona): void;
}) {
  return (
    <>
      {agents.map((agent) => {
        const direct = rooms.find((r) => r.kind === "direct" && r.agent_ids.length === 1 && r.agent_ids[0] === agent.id);
        const active = Boolean(direct && direct.thread_id === activeThreadId);
        const details = [agent.title, agent.description].filter(Boolean).join(" · ");
        return (
          <div key={agent.id} className={`es-agent-row${active ? " is-active" : ""}`}>
            <button type="button" className="es-agent-main" onClick={() => onOpenAgent(agent)} title={details ? `${agent.name} · ${details}` : agent.name}>
              <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={18} />
              <span className="es-agent-name">{agent.name}</span>
              {agent.title ? <span className="es-agent-hint">{agent.title}</span> : null}
            </button>
            <button type="button" className="es-icon-btn es-agent-edit" title={`Edit ${agent.name}`} aria-label={`Edit ${agent.name}`} onClick={() => onEditAgent(agent)}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" /></svg>
            </button>
          </div>
        );
      })}
    </>
  );
}

/** Icon-only sidebar: one face per agent. */
export function CollapsedRoster({ agents, onOpenAgent }: { agents: LeanPersona[]; onOpenAgent(agent: LeanPersona): void }) {
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
