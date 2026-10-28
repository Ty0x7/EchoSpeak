import React from "react";
import { PageShell, ago } from "./Pages";
import { AgentAvatar } from "./LeanMessage";
import { AvatarStack } from "./Roster";
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
 * Agents: your persistent AI teammates, like contacts in a messaging app. Each has its own
 * identity, instructions, memory and work history; its conversation is where you message it
 * and where it messages you (check-ins, results). Temporary workers appear inside a turn.
 */
export function AgentsHome({ agents, rooms, onOpenAgent, onEditAgent, onNewAgent, onOpenRoom, onNewGroup }: {
  agents: LeanPersona[]; rooms: LeanRoom[];
  onOpenAgent(agent: LeanPersona): void; onEditAgent(agent: LeanPersona): void; onNewAgent(): void;
  onOpenRoom(room: LeanRoom): void; onNewGroup(): void;
}) {
  const ordered = contactOrder(agents, rooms);
  const byId = new Map(agents.map(agent => [agent.id, agent]));
  const groups = rooms.filter(room => room.kind === "group")
    .sort((a, b) => (b.last_message_at || b.updated_at || 0) - (a.last_message_at || a.updated_at || 0));
  const card = (agent: LeanPersona) => {
    const room = directRoom(rooms, agent.id);
    const when = room ? Math.max(Number(room.last_message_at || 0), Number(room.updated_at || 0)) * 1000 : 0;
    return <li key={agent.id} className="es-contact">
      <button type="button" className="es-contact-main" onClick={() => onOpenAgent(agent)} aria-label={`Message ${agent.name}`}>
        <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={36} />
        <span className="es-contact-text">
          <strong>{agent.name}{agent.title ? <small>{agent.title}</small> : null}</strong>
          <span>{room?.last_preview || agent.description || "No messages yet"}</span>
        </span>
        {room?.last_message_at ? <time>{ago(when)}</time> : null}
      </button>
      <button type="button" className="es-btn es-btn-quiet es-btn-sm" onClick={() => onEditAgent(agent)} aria-label={`${agent.name}'s profile`}>Profile</button>
    </li>;
  };
  const echo = ordered.filter(agent => agent.id === "echo");
  const specialists = ordered.filter(agent => agent.id !== "echo");
  return <PageShell title="Agents" lead="Your persistent agents. Message one directly, or start a group. Each keeps its own instructions, memory and work history, and can message you when something needs you."
    action={<><button type="button" className="es-btn es-btn-quiet" onClick={onNewGroup}>New group</button><button type="button" className="es-btn es-btn-primary" onClick={onNewAgent}>New agent</button></>}>
    <div className="es-contacts">
      {echo.length ? <section aria-label="Coordinator"><h2>Coordinator</h2><ul>{echo.map(card)}</ul></section> : null}
      <section aria-label="Specialists"><h2>Specialists</h2>
        {specialists.length ? <ul>{specialists.map(card)}</ul>
          : <button type="button" className="es-page-empty" onClick={onNewAgent}><strong>No specialists yet</strong><span>Create one for research, building, writing or anything you repeat.</span></button>}
      </section>
      {groups.length ? <section aria-label="Group chats"><h2>Groups</h2><ul>{groups.map(room => {
        const members = room.agent_ids.map(id => byId.get(id)).filter(Boolean) as LeanPersona[];
        return <li key={room.id} className="es-contact">
          <button type="button" className="es-contact-main" onClick={() => onOpenRoom(room)}>
            <AvatarStack agents={members} size={22} max={3} />
            <span className="es-contact-text"><strong>{room.name}</strong><span>{room.last_preview || members.map(m => m.name).join(", ")}</span></span>
          </button>
        </li>;
      })}</ul></section> : null}
      <p className="es-contacts-note">Echo and your specialists can hand bounded tasks to temporary workers. Those show up inside the turn while they work.</p>
    </div>
  </PageShell>;
}
