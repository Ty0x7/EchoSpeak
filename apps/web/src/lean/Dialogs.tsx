import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AgentAvatar } from "./LeanMessage";
import type { LeanPersona, LeanRoom } from "./types";

function Modal({ title, subtitle, onClose, children, footer }: { title: string; subtitle?: string; onClose(): void; children: React.ReactNode; footer: React.ReactNode }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="es-modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="es-modal" role="dialog" aria-modal="true" aria-label={title}>
        <header className="es-modal-head">
          <div>
            <h2>{title}</h2>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <button type="button" className="es-icon-btn" aria-label="Close" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div className="es-modal-body">{children}</div>
        <footer className="es-modal-foot">{footer}</footer>
      </div>
    </div>,
    document.body
  );
}

const TOOLSET_INFO: Record<string, string> = {
  core: "Files and basics",
  research: "Web search and reading",
  terminal: "Run commands",
  vision: "Screenshots and vision",
  desktop: "Open apps, control the desktop",
  comms: "Email and Discord",
  memory: "Remember things",
  skills: "Installed skills and MCP",
  self: "Edit EchoSpeak itself",
};

export function AgentEditor({
  agent,
  toolsets,
  onClose,
  onSave,
  onDelete,
}: {
  agent: LeanPersona | null;
  toolsets: string[];
  onClose(): void;
  onSave(payload: Partial<LeanPersona>): Promise<void>;
  onDelete?(): Promise<void>;
}) {
  const [name, setName] = useState(agent?.name || "");
  const [title, setTitle] = useState(agent?.title || "");
  const [avatar, setAvatar] = useState(agent?.avatar || "");
  const [description, setDescription] = useState(agent?.description || "");
  const [soul, setSoul] = useState(agent?.soul || "");
  const [sets, setSets] = useState<string[]>(agent?.toolsets?.length ? agent.toolsets : ["core", "research", "memory"]);
  const [modelId, setModelId] = useState(agent?.model?.model_id || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const isEcho = agent?.id === "echo";

  const save = async () => {
    if (!name.trim()) {
      setError("Give the agent a name.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSave({
        name: name.trim(),
        title: title.trim(),
        avatar: avatar.trim(),
        description: description.trim(),
        soul,
        toolsets: sets,
        model: { provider: "", model_id: modelId.trim() },
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={agent ? `Edit ${agent.name}` : "New agent"}
      subtitle="Agents are teammates with their own personality and tools. They can chat with you directly or work together in group chats."
      onClose={onClose}
      footer={
        <>
          {agent && !agent.builtin && onDelete ? (
            <button type="button" className="es-btn es-btn-danger" disabled={busy} onClick={() => {
              if (window.confirm(`Delete ${agent.name}?`)) void onDelete().then(onClose);
            }}>Delete agent</button>
          ) : <span />}
          <div className="es-modal-actions">
            <button type="button" className="es-btn es-btn-quiet" onClick={onClose}>Cancel</button>
            <button type="button" className="es-btn es-btn-primary" disabled={busy} onClick={() => void save()}>{agent ? "Save changes" : "Create agent"}</button>
          </div>
        </>
      }
    >
      <div className="es-form-row es-form-identity">
        <AgentAvatar id={agent?.id} name={name} initials={avatar || name.slice(0, 1).toUpperCase()} size={48} />
        <label className="es-field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Scout" maxLength={40} autoFocus={!agent} />
        </label>
        <label className="es-field">
          <span>Role</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Researcher" maxLength={60} />
        </label>
        <label className="es-field es-field-narrow">
          <span>Badge</span>
          <input value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="S" maxLength={2} />
        </label>
      </div>
      <label className="es-field">
        <span>What they're good at</span>
        <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Used to decide who answers in group chats and who gets delegated work." />
      </label>
      <label className="es-field">
        <span>{isEcho ? "Personality (leave empty to use SOUL.md)" : "Personality and instructions"}</span>
        <textarea value={soul} onChange={(e) => setSoul(e.target.value)} rows={6} placeholder="How this agent talks, what it cares about, how it works." />
      </label>
      <div className="es-field">
        <span>Tools</span>
        <div className="es-chip-grid">
          {toolsets.map((set) => {
            const on = sets.includes(set);
            return (
              <button
                type="button"
                key={set}
                className={`es-toggle-chip${on ? " is-on" : ""}`}
                aria-pressed={on}
                onClick={() => setSets((prev) => (on ? prev.filter((s) => s !== set) : [...prev, set]))}
              >
                <strong>{set}</strong>
                <small>{TOOLSET_INFO[set] || ""}</small>
              </button>
            );
          })}
        </div>
      </div>
      <label className="es-field">
        <span>Model override (optional)</span>
        <input value={modelId} onChange={(e) => setModelId(e.target.value)} placeholder="Uses the selected model, e.g. qwen/qwen3.5-9b" />
      </label>
      {error ? <div className="es-form-error">{error}</div> : null}
    </Modal>
  );
}

export function RoomDialog({
  room,
  agents,
  onClose,
  onSave,
}: {
  room: LeanRoom | null;
  agents: LeanPersona[];
  onClose(): void;
  onSave(payload: { name: string; agent_ids: string[] }): Promise<void>;
}) {
  const [name, setName] = useState(room?.name || "");
  const [members, setMembers] = useState<string[]>(room?.agent_ids || agents.slice(0, 3).map((a) => a.id));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (members.length < 1) {
      setError("Pick at least one agent.");
      return;
    }
    setBusy(true);
    try {
      await onSave({ name: name.trim() || "Group chat", agent_ids: members });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={room ? "Group settings" : "New group chat"}
      subtitle="Message the whole team. @mention an agent to pick who answers, or let the group decide."
      onClose={onClose}
      footer={
        <>
          <span />
          <div className="es-modal-actions">
            <button type="button" className="es-btn es-btn-quiet" onClick={onClose}>Cancel</button>
            <button type="button" className="es-btn es-btn-primary" disabled={busy} onClick={() => void save()}>{room ? "Save" : "Create group"}</button>
          </div>
        </>
      }
    >
      <label className="es-field">
        <span>Group name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="The crew" maxLength={60} autoFocus />
      </label>
      <div className="es-field">
        <span>Members</span>
        <div className="es-member-list">
          {agents.map((agent) => {
            const on = members.includes(agent.id);
            return (
              <button
                type="button"
                key={agent.id}
                className={`es-member${on ? " is-on" : ""}`}
                aria-pressed={on}
                onClick={() => setMembers((prev) => (on ? prev.filter((id) => id !== agent.id) : [...prev, agent.id]))}
              >
                <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={30} />
                <span className="es-member-text">
                  <strong>{agent.name}</strong>
                  <small>{agent.description || agent.title}</small>
                </span>
                <span className="es-check" aria-hidden>
                  {on ? <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg> : null}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      {error ? <div className="es-form-error">{error}</div> : null}
    </Modal>
  );
}

export function RoomHeader({ room, agents, onEdit }: { room: LeanRoom; agents: LeanPersona[]; onEdit(): void }) {
  const members = room.agent_ids.map((id) => agents.find((a) => a.id === id)).filter(Boolean) as LeanPersona[];
  const solo = room.kind === "direct" ? members[0] : null;
  return (
    <div className="es-room-header">
      <div className="es-room-header-id">
        {solo ? (
          <AgentAvatar id={solo.id} name={solo.name} initials={solo.initials} size={30} />
        ) : (
          <span className="es-stack">{members.slice(0, 4).map((m) => <AgentAvatar key={m.id} id={m.id} name={m.name} initials={m.initials} size={26} />)}</span>
        )}
        <div>
          <strong>{solo ? solo.name : room.name}</strong>
          <small>{solo ? solo.title || solo.description : members.map((m) => m.name).join(" · ")}</small>
        </div>
      </div>
      {room.kind === "group" ? (
        <button type="button" className="es-btn es-btn-quiet es-btn-sm" onClick={onEdit}>Members</button>
      ) : null}
    </div>
  );
}

/** @mention suggestions. Portaled to <body> so no chat or composer container
 * can clip it; anchored to the input and opening upward unless there's no room. */
export function MentionMenu({
  query,
  agents,
  activeIndex,
  onPick,
  anchor,
}: {
  query: string;
  agents: LeanPersona[];
  activeIndex: number;
  onPick(agent: LeanPersona): void;
  anchor: HTMLElement | null;
}) {
  const matches = mentionMatches(query, agents);
  const [rect, setRect] = useState<DOMRect | null>(() => anchor?.getBoundingClientRect() ?? null);
  useEffect(() => {
    if (!anchor) return;
    const update = () => setRect(anchor.getBoundingClientRect());
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [anchor]);
  if (!matches.length || !rect) return null;
  const estimated = Math.min(6, matches.length) * 38 + 12;
  const openUp = rect.top >= estimated + 12 || rect.top > window.innerHeight - rect.bottom;
  const style: React.CSSProperties = {
    left: Math.max(8, Math.min(rect.left, window.innerWidth - 268)),
    ...(openUp ? { bottom: window.innerHeight - rect.top + 8 } : { top: rect.bottom + 8 }),
  };
  return createPortal(
    <div className="es-mention-menu" role="listbox" aria-label="Mention an agent" style={style} data-direction={openUp ? "up" : "down"}>
      {matches.map((agent, index) => (
        <button
          type="button"
          role="option"
          aria-selected={index === activeIndex}
          key={agent.id}
          className={`es-mention${index === activeIndex ? " is-active" : ""}`}
          onMouseDown={(event) => {
            event.preventDefault();
            onPick(agent);
          }}
        >
          <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={22} />
          <strong>{agent.name}</strong>
          <small>{agent.title}</small>
        </button>
      ))}
    </div>,
    document.body
  );
}

export function mentionMatches(query: string, agents: LeanPersona[]): LeanPersona[] {
  const q = query.toLowerCase();
  return agents.filter((a) => a.name.toLowerCase().startsWith(q) || a.id.startsWith(q)).slice(0, 6);
}

/** Return the @word being typed at the caret, if any. */
export function activeMention(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const match = /(^|\s)@([\w-]{0,30})$/.exec(before);
  if (!match) return null;
  return { start: caret - match[2].length - 1, query: match[2] };
}
