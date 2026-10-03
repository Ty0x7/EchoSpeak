import React from "react";

/** Technical diagrams for the docs (moved from the old front page). */

const flow = [
  ["01", "Route", "In a group chat, @mentions decide who answers. Otherwise a quick model call picks the best agent, and follow-ups stay with whoever answered last."],
  ["02", "Work", "The agent's model calls tools, reads the results and keeps going until it can answer. Errors go back to the model so it can try another way."],
  ["03", "Stream", "Thinking, tool calls and the reply stream into the chat as they happen, one message per agent turn."],
  ["04", "Save", "Every message is saved with its steps, so reopening a chat shows exactly what happened."],
];

export function GroupChatDiagram() {
  return (
    <div className="flow-diagram" role="img" aria-label="Group chat flow. You ask Jarvis and Glados; both answer at the same time on their own; then Echo, the room's lead, posts a summary. Separately, an agent can hand a task to a teammate and continue after their reply.">
      <div className="flow-col">
        <span className="flow-kicker">You</span>
        <div className="flow-node flow-node-user">@Jarvis @Glados which option should I use?</div>
      </div>
      <div className="flow-arrow" aria-hidden="true" />
      <div className="flow-col">
        <span className="flow-kicker">At the same time, independently</span>
        <div className="flow-node flow-node-working"><b className="mini-face" aria-hidden="true"><i /><i /></b><span><strong>Jarvis</strong>Answers on its own</span></div>
        <div className="flow-node flow-node-working flow-node-late"><b className="mini-face" aria-hidden="true"><i /><i /></b><span><strong>Glados</strong>Answers on its own</span></div>
      </div>
      <div className="flow-arrow" aria-hidden="true" />
      <div className="flow-col">
        <span className="flow-kicker">Room lead</span>
        <div className="flow-node flow-node-strong"><b className="mini-face mini-face-echo" aria-hidden="true"><i /><i /></b><span><strong>Echo · Summary</strong>Where they agree, where they differ, what to do</span></div>
      </div>
      <div className="flow-handoff">
        <span className="flow-kicker">Handoffs</span>
        <p><strong>Echo</strong> asks Jarvis <i aria-hidden="true">→</i> <strong>Jarvis</strong> replies, marked “via Echo” <i aria-hidden="true">→</i> <strong>Echo</strong> continues below. Each turn is its own message, in the order it happened, with a limit on how many handoffs one question can trigger.</p>
      </div>
    </div>
  );
}

export function SystemDiagram() {
  const node = (x: number, y: number, w: number, h: number, title: string, lines: string[], tag?: string, strong = false) => (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={14} className={strong ? "sd-node sd-node-strong" : "sd-node"} />
      <text x={x + 22} y={y + 36} className="sd-title">{title}</text>
      {lines.map((line, index) => (
        <text key={line} x={x + 22} y={y + 62 + index * 21} className="sd-sub">{line}</text>
      ))}
      {tag ? <text x={x + w - 22} y={y + 36} textAnchor="end" className="sd-tag">{tag}</text> : null}
    </g>
  );
  const tool = (x: number, y: number, label: string) => (
    <g key={label}>
      <rect x={x} y={y} width={136} height={46} rx={10} className="sd-chip" />
      <text x={x + 68} y={y + 29} textAnchor="middle" className="sd-chip-text">{label}</text>
    </g>
  );
  return (
    <figure className="system-diagram" aria-label="How EchoSpeak is put together">
      <svg viewBox="0 0 1200 500" role="img" aria-labelledby="sd-title sd-desc">
        <title id="sd-title">EchoSpeak system overview</title>
        <desc id="sd-desc">
          On your PC, the EchoSpeak window talks to a local backend over HTTP with a live stream. The backend routes each message,
          runs the agent loop and asks for approvals; it uses tools (files and projects, terminal or Docker, web search, memory) and
          saves every message to disk. It calls a model provider through an OpenAI-compatible API: a local server such as LM Studio or
          Ollama, or a cloud API such as OpenAI or Gemini.
        </desc>
        <defs>
          <marker id="sd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 10 5 0 10z" className="sd-arrowhead" />
          </marker>
        </defs>

        <rect x={16} y={20} width={824} height={462} rx={22} className="sd-boundary" />
        <text x={44} y={52} className="sd-kicker">YOUR PC</text>

        {node(44, 80, 300, 120, "EchoSpeak window", ["Chat, agents, group chats", "Projects, voice, settings"], "TAURI · REACT")}
        {node(500, 80, 312, 120, "Local backend", ["Routes each message", "Runs the agent loop, asks approvals"], "PYTHON", true)}
        {node(44, 330, 300, 120, "On disk", ["Chats with every step", "Memory, agents, settings"])}

        {/* Pulses that travel the connectors. */}
        <path id="sd-p1" d="M344 140 H500" className="sd-flow" />
        <path id="sd-p2" d="M812 140 H900" className="sd-flow" />
        <path id="sd-p3" d="M656 200 V250 H581 V280" className="sd-flow" />
        <path id="sd-p4" d="M530 200 V230 H194 V330" className="sd-flow" />
        {["sd-p1", "sd-p2", "sd-p3", "sd-p4"].map((id, index) => (
          <circle key={id} r={3.2} className="sd-pulse">
            <animateMotion dur={`${2.6 + index * 0.4}s`} begin={`${index * 0.7}s`} repeatCount="indefinite">
              <mpath href={`#${id}`} />
            </animateMotion>
          </circle>
        ))}
        {/* window <-> backend */}
        <line x1={344} y1={140} x2={500} y2={140} className="sd-line" markerStart="url(#sd-arrow)" markerEnd="url(#sd-arrow)" />
        <text x={422} y={128} textAnchor="middle" className="sd-label">HTTP + LIVE STREAM</text>

        {/* backend -> tools */}
        <path d="M656 200 V250 M581 250 H731 M581 250 V280 M731 250 V280" className="sd-line" />
        {tool(513, 280, "Files & projects")}
        {tool(663, 280, "Terminal / Docker")}
        {tool(513, 342, "Web search")}
        {tool(663, 342, "Memory")}
        <text x={656} y={420} textAnchor="middle" className="sd-label">TOOLS</text>

        {/* backend -> disk */}
        <path d="M530 200 V230 H194 V330" className="sd-line" markerEnd="url(#sd-arrow)" />
        <text x={360} y={220} textAnchor="middle" className="sd-label">SAVES EVERY MESSAGE</text>

        {/* backend -> provider */}
        <line x1={812} y1={140} x2={900} y2={140} className="sd-line" markerEnd="url(#sd-arrow)" />
        <text x={856} y={128} textAnchor="middle" className="sd-label">MODEL CALLS</text>
        {node(900, 80, 284, 150, "Model provider", ["Local: LM Studio, Ollama,", "llama.cpp, vLLM, LocalAI", "Cloud: OpenAI, Gemini"], undefined, true)}
        <text x={1042} y={262} textAnchor="middle" className="sd-note">Each agent can use its own model</text>
      </svg>

      <ol className="system-stack" aria-hidden="true">
        <li><strong>EchoSpeak window</strong><span>Chat, agents, group chats, projects, voice</span></li>
        <li className="is-link">HTTP + live stream</li>
        <li className="is-strong"><strong>Local backend</strong><span>Routes each message, runs the agent loop, asks approvals</span></li>
        <li className="is-link">Tools · saves to disk</li>
        <li><strong>Files · Terminal · Web search · Memory</strong><span>Chats, memory and settings stay on your PC</span></li>
        <li className="is-link">Model calls</li>
        <li className="is-strong"><strong>Model provider</strong><span>LM Studio, Ollama, llama.cpp, vLLM, LocalAI, or OpenAI / Gemini</span></li>
      </ol>
    </figure>
  );
}

export function MessageTimeline() {
  return (
    <ol className="timeline" aria-label="What happens to a message">
      {flow.map(([number, title, copy]) => (
        <li key={number}>
          <span className="timeline-dot">{number}</span>
          <h3>{title}</h3>
          <p>{copy}</p>
        </li>
      ))}
    </ol>
  );
}

