import React from "react";
import { AgentAvatar } from "./LeanMessage";
import type { LeanPersona, LeanRoom } from "./types";

/** The agent's one-to-one conversation (its "contact"), if it has one yet. */
export function directRoom(rooms: LeanRoom[], agentId: string): LeanRoom | undefined {
  return rooms.find(room => room.kind === "direct" && room.agent_ids.length === 1 && room.agent_ids[0] === agentId);
}

/** Echo first (it coordinates), then specialists; most recently messaged first within each. */
export function contactOrder(agents: LeanPersona[], rooms: LeanRoom[]): LeanPersona[] {
  const last = (agent: LeanPersona) => {
    const room = directRoom(rooms, agent.id);
    return room ? Math.max(Number(room.last_message_at || 0), Number(room.updated_at || 0)) : 0;
  };
  return agents.slice().sort((a, b) => Number(b.id === "echo") - Number(a.id === "echo") || last(b) - last(a));
}

/**
 * Agents with no conversation open: a short start screen. The roster lives in the sidebar, so this
 * doesn't repeat it. Entering Agents normally reopens the conversation you left (index.tsx).
 */
export function AgentsHome({ agents, onOpenAgent, onNewAgent, onNewGroup }: {
  agents: LeanPersona[]; rooms: LeanRoom[];
  onOpenAgent(agent: LeanPersona): void; onEditAgent(agent: LeanPersona): void; onNewAgent(): void;
  onOpenRoom(room: LeanRoom): void; onNewGroup(): void;
}) {
  const echo = agents.find(agent => agent.id === "echo");
  return <div className="es-agents-start" role="region" aria-label="Agents">
    <div className="es-agents-start-card">
      <span className="es-agents-start-mark" aria-hidden><AgentAvatar id="new" size={40} /></span>
      <h1>New agent</h1>
      <p>A persistent teammate with its own instructions, tools and memory.</p>
      <div className="es-agents-start-actions">
        <button type="button" className="es-btn es-btn-primary" onClick={onNewAgent}>Create agent</button>
        <button type="button" className="es-btn es-btn-quiet" onClick={onNewGroup}>New group</button>
        {echo ? <button type="button" className="es-btn es-btn-quiet" onClick={() => onOpenAgent(echo)}>Message {echo.name}</button> : null}
      </div>
    </div>
  </div>;
}
