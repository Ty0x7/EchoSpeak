import React, { useEffect, useRef, useState } from "react";
import { EchoFace, EchoSays, echoFaceStyles } from "./components/EchoFace";

const githubUrl = import.meta.env.VITE_GITHUB_URL || "https://github.com/Ty0x7/EchoSpeak";
const releasesUrl = `${githubUrl}/releases`;
const installerUrl = import.meta.env.VITE_DESKTOP_DOWNLOAD_URL || `${releasesUrl}/latest`;
const appVersion = String(import.meta.env.VITE_APP_VERSION || "8.0.0");
const downloadLabel = "Download for Windows";

type IconName = "chat" | "team" | "research" | "code" | "memory" | "voice" | "model" | "shield" | "windows" | "github" | "arrow";

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.65, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "chat") return <svg {...common}><path d="M5 18.5 3.5 21l.7-4A8.5 8.5 0 1 1 12 20.5H8" /><path d="M8 10h8M8 14h5" /></svg>;
  if (name === "team") return <svg {...common}><circle cx="8" cy="8" r="3" /><circle cx="16.5" cy="9.5" r="2.5" /><path d="M2.5 19a5.5 5.5 0 0 1 11 0M13.5 18.5a4.5 4.5 0 0 1 8-2.8" /></svg>;
  if (name === "research") return <svg {...common}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5M8 11h6M11 8v6" /></svg>;
  if (name === "code") return <svg {...common}><path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14" /></svg>;
  if (name === "memory") return <svg {...common}><path d="M7 4.5A3.5 3.5 0 0 0 5.5 11v2A3.5 3.5 0 0 0 7 19.5M17 4.5A3.5 3.5 0 0 1 18.5 11v2a3.5 3.5 0 0 1-1.5 6.5M9 4v16M15 4v16M9 8h6M9 16h6" /></svg>;
  if (name === "voice") return <svg {...common}><rect x="8" y="3" width="8" height="12" rx="4" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" /></svg>;
  if (name === "model") return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M9 9h6v6H9zM9 1.5V4M15 1.5V4M9 20v2.5M15 20v2.5M1.5 9H4M1.5 15H4M20 9h2.5M20 15h2.5" /></svg>;
  if (name === "shield") return <svg {...common}><path d="M12 3 4.5 6v5.5c0 4.4 3 7.7 7.5 9.5 4.5-1.8 7.5-5.1 7.5-9.5V6L12 3Z" /><path d="m9 12 2 2 4-4" /></svg>;
  if (name === "windows") return <svg {...common}><path d="M3 5.5 10.5 4v7H3v-5.5ZM13 3.5 21 2v9h-8V3.5ZM3 13h7.5v7L3 18.5V13ZM13 13h8v9l-8-1.5V13Z" /></svg>;
  if (name === "github") return <svg {...common}><path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21" /></svg>;
  return <svg {...common}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
}

/** What the app does today. Keep in step with docs/ARCHITECTURE.md. */
const capabilities: Array<{ icon: IconName; title: string; copy: string; meta: string }> = [
  { icon: "chat", title: "An agent that finishes the job", copy: "Echo keeps calling tools until it has an answer, and you watch every step as it happens: thinking, searches, file reads, commands.", meta: "Agent loop · live timeline" },
  { icon: "team", title: "Agents and group chats", copy: "Echo, Scout and Forge come built in, and you can make your own. Put them in a group chat, @mention who should answer, or let the group decide.", meta: "Personas · rooms · handoffs" },
  { icon: "code", title: "Projects and code", copy: "Attach a folder and agents can read, search and edit its files, and run commands in PowerShell or an optional Docker sandbox.", meta: "Files · terminal · sandbox" },
  { icon: "research", title: "Research with sources", copy: "Web search and page reading, with links to the pages actually read in the answer.", meta: "Search · read · cite" },
  { icon: "memory", title: "Memory on your machine", copy: "Echo remembers facts you share and recalls them when they matter. Chats, memory and settings are stored locally.", meta: "Local storage" },
  { icon: "voice", title: "Voice", copy: "Talk with the microphone, with local transcription when Whisper is set up, and have replies read aloud. Wake word is not available yet.", meta: "Mic · read aloud" },
  { icon: "model", title: "Your choice of model", copy: "Run models locally with LM Studio, Ollama, llama.cpp, vLLM or LocalAI, or use an OpenAI or Gemini API key. Each agent can use its own model.", meta: "Local or cloud" },
  { icon: "shield", title: "Approvals where they matter", copy: "Reading and searching just run. Deleting files, sending messages and controlling your desktop wait for your OK.", meta: "Smart approvals" },
];

const flow = [
  ["01", "Route", "In a group chat, @mentions decide who answers. Otherwise a quick model call picks the best agent, and follow-ups stay with whoever answered last."],
  ["02", "Work", "The agent's model calls tools, reads the results and keeps going until it can answer. Errors go back to the model so it can try another way."],
  ["03", "Stream", "Thinking, tool calls and the reply stream into the chat as they happen, one message per agent turn."],
  ["04", "Save", "Every message is saved with its steps, so reopening a chat shows exactly what happened."],
];

const requirements: Array<[string, string]> = [
  ["System", "Windows 10 or 11, 64-bit"],
  ["Disk", "About 1.5 GB for the app"],
  ["Memory", "8 GB RAM (16 GB for local models)"],
  ["Models", "LM Studio or Ollama (8 GB VRAM GPU for 4B–9B models), or an OpenAI / Gemini key"],
  ["Optional", "Docker Desktop, for the sandbox terminal"],
];

const chapters = [
  ["01", "What it does", "#capabilities"],
  ["02", "Group chats", "#agents"],
  ["03", "How it works", "#how-it-works"],
  ["04", "Download", "#download"],
];

/** Adds data-visible once the element scrolls into view (immediately with reduced motion). */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -12% 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { ref, visible };
}

function SectionHeading({ id, number, label, lines, lede }: { id: string; number: string; label: string; lines: [string, string]; lede?: string }) {
  const { ref, visible } = useReveal<HTMLDivElement>();
  return (
    <div className="sh" ref={ref} data-visible={visible ? "true" : "false"}>
      <span className="sh-num" aria-hidden="true">{number}</span>
      <div className="sh-label">
        <ChapterLabel number={number}>{label}</ChapterLabel>
      </div>
      <div className="sh-body">
        <h2 id={id}>
          <span className="sh-line"><span>{lines[0]}</span></span>
          <span className="sh-line sh-line-muted"><span>{lines[1]}</span></span>
        </h2>
        {lede ? <p className="sh-lede">{lede}</p> : null}
      </div>
    </div>
  );
}

/** Echo at the centre, the ways he helps orbiting around him. */
function EchoOrbit() {
  const labels = ["Chat", "Group chats", "Projects", "Research", "Memory", "Voice"];
  return (
    <div className="orbit-feature">
      <div className="orbit-feature-copy">
        <span>One Echo · many ways to work</span>
        <h3 id="capabilities-title">Echo changes how he helps without changing who he is.</h3>
        <p>Ask a question, start research, attach a project, speak aloud, or bring Scout and Forge in. The surface changes; the chat, memory and history stay connected.</p>
      </div>
      <div className="orbit-stage" aria-label="Chat, group chats, projects, research, memory and voice, all around Echo">
        <div className="orbit-ring orbit-ring-a" aria-hidden="true" />
        <div className="orbit-ring orbit-ring-b" aria-hidden="true" />
        <div className="orbit-ring orbit-ring-c" aria-hidden="true" />
        <div className="orbit-core"><EchoFace size={84} /></div>
        {labels.map((label, index) => (
          <span className={`orbit-label orbit-label-${index}`} key={label} style={{ animationDelay: `${index * -1.1}s` }}>{label}</span>
        ))}
      </div>
    </div>
  );
}

function ChapterLabel({ number, children }: { number: string; children: React.ReactNode }) {
  return <p className="section-index"><strong>{number}</strong><span>{children}</span></p>;
}

function EchoStage() {
  return (
    <div className="echo-stage">
      <div className="echo-stage-says"><EchoSays /></div>
      <EchoFace size="clamp(150px, 46vw, 260px)" className="echo-stage-face" />
      <div className="echo-stage-foot">
        <div className="echo-stage-who"><strong>Echo</strong><span>Personal agent</span></div>
        <div className="echo-stage-team" aria-label="Teammates">
          <span><b>S</b>Scout</span>
          <span><b>F</b>Forge</span>
        </div>
      </div>
    </div>
  );
}

function GroupChatDiagram() {
  return (
    <div className="flow-diagram" role="img" aria-label="Group chat flow. You ask Scout and Forge; both answer at the same time on their own; then Echo, the room's lead, posts a summary. Separately, an agent can hand a task to a teammate and continue after their reply.">
      <div className="flow-col">
        <span className="flow-kicker">You</span>
        <div className="flow-node flow-node-user">@Scout @Forge which option should I use?</div>
      </div>
      <div className="flow-arrow" aria-hidden="true" />
      <div className="flow-col">
        <span className="flow-kicker">At the same time, independently</span>
        <div className="flow-node flow-node-working"><b className="mini-face" aria-hidden="true"><i /><i /></b><span><strong>Scout</strong>Answers on its own</span></div>
        <div className="flow-node flow-node-working flow-node-late"><b className="mini-face" aria-hidden="true"><i /><i /></b><span><strong>Forge</strong>Answers on its own</span></div>
      </div>
      <div className="flow-arrow" aria-hidden="true" />
      <div className="flow-col">
        <span className="flow-kicker">Room lead</span>
        <div className="flow-node flow-node-strong"><b className="mini-face mini-face-echo" aria-hidden="true"><i /><i /></b><span><strong>Echo · Summary</strong>Where they agree, where they differ, what to do</span></div>
      </div>
      <div className="flow-handoff">
        <span className="flow-kicker">Handoffs</span>
        <p><strong>Echo</strong> asks Scout <i aria-hidden="true">→</i> <strong>Scout</strong> replies, marked “via Echo” <i aria-hidden="true">→</i> <strong>Echo</strong> continues below. Each turn is its own message, in the order it happened, with a limit on how many handoffs one question can trigger.</p>
      </div>
    </div>
  );
}

function SystemDiagram() {
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

function MessageTimeline() {
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

export function Marketing() {
  return (
    <div className="marketing-site">
      <style>{styles + echoFaceStyles}</style>

      <header className="site-header">
        <a className="wordmark" href="#top" aria-label="EchoSpeak home">
          <span className="wordmark-face"><span /><span /></span>
          <span>EchoSpeak</span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#capabilities">What it does</a>
          <a href="#agents">Group chats</a>
          <a href="#how-it-works">How it works</a>
          <a href="#download">Download</a>
        </nav>
        <a className="header-install" href={installerUrl}>{downloadLabel} <Icon name="arrow" size={16} /></a>
      </header>

      <main id="top">
        <section className="hero section-shell">
          <div className="hero-copy">
            <p className="eyebrow"><span>EchoSpeak {appVersion}</span><span>Windows desktop</span></p>
            <h1>Your computer.<br />Your context.<br /><em>Your agents.</em></h1>
            <p className="hero-lede">A local-first assistant with a team of agents. Chat with Echo, bring Scout and Forge into a group chat, attach a project folder, and watch every step: thinking, tools and handoffs.</p>
            <div className="hero-actions">
              <a className="button button-primary" href={installerUrl}><Icon name="windows" />{downloadLabel} <Icon name="arrow" size={17} /></a>
              <a className="button button-secondary" href={githubUrl} target="_blank" rel="noreferrer"><Icon name="github" />View on GitHub</a>
            </div>
            <div className="hero-details">
              <span>Windows 10 / 11</span>
              <span>Local or cloud models</span>
              <span>MIT licensed</span>
            </div>
          </div>
          <EchoStage />
        </section>

        <nav className="chapter-nav section-shell" aria-label="Explore EchoSpeak">
          {chapters.map(([number, label, href]) => (
            <a href={href} key={number}>
              <span>{number}</span>
              <strong>{label}</strong>
              <i aria-hidden="true" />
            </a>
          ))}
        </nav>

        <section className="capability-section section-shell" id="capabilities" aria-labelledby="capabilities-title">
          <EchoOrbit />
          <div className="capability-grid">
            {capabilities.map((capability) => (
              <article className="capability-card" key={capability.title}>
                <Icon name={capability.icon} size={20} />
                <h3>{capability.title}</h3>
                <p>{capability.copy}</p>
                <span>{capability.meta}</span>
              </article>
            ))}
          </div>
        </section>

        <section className="agents-section section-shell" id="agents" aria-labelledby="agents-title">
          <SectionHeading
            id="agents-title"
            number="02"
            label="Group chats"
            lines={["Ask the team.", "Get one clear answer."]}
            lede="Mention several agents and they answer side by side, each on their own; the room's lead then sums up. Agents on different models take turns instead, so a local GPU never loads two models at once."
          />
          <GroupChatDiagram />
        </section>

        <section className="architecture-section section-shell" id="how-it-works" aria-labelledby="architecture-title">
          <SectionHeading
            id="architecture-title"
            number="03"
            label="How it works"
            lines={["Simple on the surface.", "Visible underneath."]}
            lede="Everything runs on your PC: the app, the agents and your data. Only the model can be local or in the cloud."
          />
          <SystemDiagram />
          <MessageTimeline />
        </section>

        <section className="download-section section-shell" id="download" aria-labelledby="download-title">
          <div className="download-mark" aria-hidden="true"><span className="download-face"><i /><i /></span></div>
          <div className="download-copy">
            <ChapterLabel number="04">Download</ChapterLabel>
            <h2 id="download-title">Bring Echo home.</h2>
            <p>Install the Windows app, pick a model (local or cloud) in Settings, and start a chat.</p>
            <div className="download-actions">
              <a className="button button-light" href={installerUrl}><Icon name="windows" />{downloadLabel} <Icon name="arrow" size={17} /></a>
              <a className="text-link" href={releasesUrl} target="_blank" rel="noreferrer">Release notes <Icon name="arrow" size={15} /></a>
              <a className="text-link" href={githubUrl} target="_blank" rel="noreferrer"><Icon name="github" size={18} />Source</a>
            </div>
            <small className="download-note">EchoSpeak {appVersion} · Windows x64 installer (.exe) or .msi · macOS and Linux builds are not available yet.</small>
            <dl className="requirements" aria-label="System requirements">
              {requirements.map(([label, value]) => (
                <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
              ))}
            </dl>
          </div>
        </section>
      </main>

      <footer className="site-footer section-shell">
        <a className="wordmark" href="#top"><span className="wordmark-face"><span /><span /></span><span>EchoSpeak</span></a>
        <p>Local-first personal agents for Windows.</p>
        <div><a href={githubUrl} target="_blank" rel="noreferrer">GitHub</a><a href={releasesUrl} target="_blank" rel="noreferrer">Releases</a><a href="#download">Download</a></div>
      </footer>
    </div>
  );
}

const styles = `
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body { margin: 0; background: #050505; }
  .marketing-site { --black: #050505; --line: #272727; --white: #f4f4f2; min-height: 100vh; overflow-x: hidden; background: var(--black); color: var(--white); font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }
  .marketing-site a { color: inherit; text-decoration: none; }
  .marketing-site a:focus-visible { outline: 2px solid rgba(255,255,255,.75); outline-offset: 3px; border-radius: 6px; }
  .section-shell { width: min(1360px, calc(100% - 80px)); margin-inline: auto; }
  .site-header { position: fixed; inset: 0 0 auto; z-index: 100; height: 72px; padding: 0 max(40px, calc((100vw - 1360px) / 2)); display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; border-bottom: 1px solid rgba(255,255,255,.08); background: rgba(5,5,5,.84); backdrop-filter: blur(20px); }
  .wordmark { display: inline-flex; align-items: center; gap: 11px; width: max-content; font-weight: 650; letter-spacing: -.025em; font-size: 17px; }
  .wordmark-face { width: 25px; height: 25px; display: flex; align-items: center; justify-content: center; gap: 4px; border-radius: 7px; background: var(--white); }
  .wordmark-face span { width: 3px; height: 5px; border-radius: 3px; background: var(--black); }
  .site-header nav { display: flex; align-items: center; gap: 30px; color: #ababaa; font-size: 13px; }
  .site-header nav a, .site-footer a { transition: color .2s ease; }
  .site-header nav a:hover, .site-footer a:hover { color: #fff; }
  .header-install { justify-self: end; display: inline-flex; align-items: center; gap: 10px; padding: 10px 15px; border: 1px solid #393939; border-radius: 9px; font-size: 13px; font-weight: 600; background: #101010; transition: background .2s ease, border-color .2s ease; }
  .header-install:hover { background: #1a1a1a; border-color: #666; }

  .hero { padding-top: 140px; padding-bottom: 90px; display: grid; grid-template-columns: minmax(0, .82fr) minmax(0, 1.18fr); gap: clamp(40px, 5vw, 80px); align-items: center; }
  .eyebrow { margin: 0 0 28px; display: flex; gap: 16px; align-items: center; color: #b8b8b5; font-family: "SFMono-Regular", Consolas, monospace; font-size: 11px; letter-spacing: .11em; text-transform: uppercase; }
  .eyebrow span + span { padding-left: 16px; border-left: 1px solid #393939; color: #8a8a86; }
  .hero h1 { margin: 0; font-size: clamp(54px, 5.6vw, 96px); line-height: .93; letter-spacing: -.065em; font-weight: 620; }
  .hero h1 em { color: #8a8a86; font-style: normal; }
  .hero-lede { max-width: 560px; margin: 30px 0 0; color: #b0b0ac; font-size: clamp(16px, 1.25vw, 19px); line-height: 1.6; letter-spacing: -.012em; }
  .hero-actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 36px; }
  .button { min-height: 52px; padding: 0 20px; display: inline-flex; align-items: center; justify-content: center; gap: 11px; border: 1px solid #333; border-radius: 11px; font-size: 14px; font-weight: 650; transition: transform .2s ease, background .2s ease, border-color .2s ease; }
  .button:hover { transform: translateY(-1px); }
  .button-primary { background: var(--white); color: #070707 !important; border-color: var(--white); }
  .button-primary:hover { background: #fff; }
  .button-secondary { background: #0b0b0b; color: #dededb; }
  .button-secondary:hover { background: #141414; border-color: #5b5b5b; }
  .hero-details { display: flex; flex-wrap: wrap; gap: 20px; margin-top: 24px; color: #8a8a86; font-family: "SFMono-Regular", Consolas, monospace; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
  .hero-details span { display: inline-flex; align-items: center; gap: 8px; }
  .hero-details span::before { content: ""; width: 3px; height: 3px; border-radius: 50%; background: #898985; }

  .echo-stage { position: relative; min-height: 560px; padding: 34px; display: grid; grid-template-rows: auto 1fr auto; justify-items: center; align-items: center; border: 1px solid #222; border-radius: 26px; background: #090909; overflow: hidden; }
  .echo-stage-says { justify-self: end; margin-right: 6%; }
  .echo-stage-face { margin-top: -10px; }
  .echo-stage-foot { width: 100%; display: flex; align-items: end; justify-content: space-between; gap: 16px; }
  .echo-stage-who { display: grid; gap: 4px; }
  .echo-stage-who strong { font-size: 17px; font-weight: 650; letter-spacing: -.02em; }
  .echo-stage-who span, .echo-stage-team span { color: #8a8a86; font-family: "SFMono-Regular", Consolas, monospace; font-size: 9.5px; letter-spacing: .09em; text-transform: uppercase; }
  .echo-stage-team { display: flex; gap: 8px; }
  .echo-stage-team span { display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px 6px 6px; border: 1px solid #262626; border-radius: 999px; background: #0e0e0e; }
  .echo-stage-team b { width: 20px; height: 20px; display: grid; place-items: center; border: 1px solid #3a3a3a; border-radius: 6px; background: #171717; color: #eee; font-family: Inter, sans-serif; font-size: 10px; letter-spacing: 0; }

  .chapter-nav { display: grid; grid-template-columns: repeat(4, 1fr); border-bottom: 1px solid #1c1c1c; }
  .chapter-nav a { position: relative; min-height: 76px; padding: 26px 4px 22px; display: grid; grid-template-columns: 30px 1fr; gap: 8px; align-items: start; color: #8a8a86; transition: color .2s ease; }
  .chapter-nav a:hover { color: #f3f3f0; }
  .chapter-nav span { color: #8a8a86; font-family: monospace; font-size: 10px; }
  .chapter-nav strong { font-size: 12.5px; font-weight: 560; letter-spacing: -.01em; }
  .chapter-nav i { position: absolute; left: 4px; right: 24px; bottom: -1px; height: 1px; }
  .chapter-nav i::after { content: ""; display: block; width: 0; height: 1px; background: #e7e7e3; transition: width .3s ease; }
  .chapter-nav a:hover i::after { width: 100%; }

  .section-index { margin: 0; display: inline-flex; align-items: center; gap: 12px; color: #8a8a86; font-family: "SFMono-Regular", Consolas, monospace; font-size: 9.5px; letter-spacing: .11em; text-transform: uppercase; }
  .section-index strong { width: 31px; height: 31px; display: grid; place-items: center; flex: 0 0 auto; border: 1px solid #333; border-radius: 50%; color: #d3d3cf; font-size: 9px; font-weight: 500; }

  .sh { position: relative; isolation: isolate; }
  .sh-num { position: absolute; z-index: -1; right: -6px; top: -64px; font-size: clamp(140px, 18vw, 260px); font-weight: 700; line-height: 1; letter-spacing: -.08em; color: transparent; -webkit-text-stroke: 1px #1d1d1d; user-select: none; pointer-events: none; transition: transform 1.2s cubic-bezier(.2,.7,0,1), opacity 1.2s ease; opacity: 0; transform: translateY(24px); }
  .sh[data-visible="true"] .sh-num { opacity: 1; transform: none; }
  .sh-label { display: flex; align-items: center; gap: 18px; }
  .sh-body { margin-top: 22px; display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: clamp(28px, 5vw, 80px); align-items: end; }
  .sh h2 { margin: 0; font-size: clamp(44px, 5.2vw, 78px); line-height: 1.02; letter-spacing: -.058em; font-weight: 580; }
  .sh-line { display: block; overflow: hidden; padding-bottom: .06em; }
  .sh-line > span { display: inline-block; transform: translateY(105%); transition: transform .9s cubic-bezier(.2,.7,0,1); }
  .sh-line-muted > span { color: #85857f; transition-delay: .12s; }
  .sh[data-visible="true"] .sh-line > span { transform: none; }
  .sh-lede { margin: 0 0 8px; max-width: 460px; color: #a1a19d; font-size: 16px; line-height: 1.65; opacity: 0; transform: translateY(10px); transition: opacity .8s ease .3s, transform .8s cubic-bezier(.2,.7,0,1) .3s; }
  .sh[data-visible="true"] .sh-lede { opacity: 1; transform: none; }

  .orbit-feature { margin-top: 0; min-height: 360px; padding: 40px 46px; display: grid; grid-template-columns: 1fr 1fr; gap: 30px; align-items: center; border: 1px solid #2a2a2a; border-radius: 20px; background: #0b0b0b; overflow: hidden; }
  .orbit-feature-copy > span { color: #85857f; font-family: monospace; font-size: 9.5px; letter-spacing: .1em; text-transform: uppercase; }
  .orbit-feature-copy h3 { max-width: 540px; margin: 18px 0 16px; font-size: clamp(28px, 3vw, 44px); line-height: 1.05; letter-spacing: -.045em; font-weight: 580; }
  .orbit-feature-copy p { max-width: 500px; margin: 0; color: #9a9a96; font-size: 14.5px; line-height: 1.65; }
  .orbit-stage { position: relative; justify-self: center; width: min(100%, 420px); height: 300px; }
  .orbit-ring { position: absolute; top: 50%; left: 50%; border: 1px solid #2c2c2b; border-radius: 50%; }
  .orbit-ring-a { width: 230px; height: 110px; animation: orbitSpinA 26s linear infinite; }
  .orbit-ring-b { width: 330px; height: 160px; opacity: .75; animation: orbitSpinB 34s linear infinite; }
  .orbit-ring-c { width: 400px; height: 220px; opacity: .35; border-style: dashed; animation: orbitSpinA 48s linear infinite reverse; }
  @keyframes orbitSpinA { from { transform: translate(-50%, -50%) rotate(-18deg); } to { transform: translate(-50%, -50%) rotate(342deg); } }
  @keyframes orbitSpinB { from { transform: translate(-50%, -50%) rotate(28deg); } to { transform: translate(-50%, -50%) rotate(388deg); } }
  .orbit-core { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -54%); z-index: 2; }
  .orbit-label { position: absolute; z-index: 3; padding: 6px 10px; border: 1px solid #30302f; border-radius: 7px; background: #0d0d0d; color: #c4c4c0; font-family: monospace; font-size: 9.5px; letter-spacing: .07em; text-transform: uppercase; white-space: nowrap; animation: orbitBob 6.6s ease-in-out infinite; }
  .orbit-label-0 { top: 10px; left: 50%; margin-left: -24px; }
  .orbit-label-1 { top: 62px; right: 0; }
  .orbit-label-2 { bottom: 54px; right: 12px; }
  .orbit-label-3 { bottom: 4px; left: 50%; margin-left: -38px; }
  .orbit-label-4 { bottom: 54px; left: 8px; }
  .orbit-label-5 { top: 62px; left: 0; }
  @keyframes orbitBob { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }

  .sd-flow { fill: none; stroke: none; }
  .sd-pulse { fill: #f2f2ef; filter: drop-shadow(0 0 4px rgba(255,255,255,.55)); }
  .capability-section { padding-block: 64px 32px; }
  .agents-section, .architecture-section { padding-block: 64px; }
  .capability-grid { margin-top: 12px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  .capability-card { min-height: 224px; padding: 24px 22px 20px; display: flex; flex-direction: column; gap: 12px; border-radius: 16px; background: #0b0b0b; color: #c8c8c4; transition: background .2s ease, transform .2s ease; }
  .capability-card:hover { background: #101010; transform: translateY(-2px); }
  .capability-card svg { color: #e2e2de; }
  .capability-card h3 { margin: 6px 0 0; font-size: 16px; font-weight: 600; letter-spacing: -.02em; color: #f2f2ef; }
  .capability-card p { margin: 0; color: #9a9a96; font-size: 13.5px; line-height: 1.6; }
  .capability-card span { margin-top: auto; color: #7c7c78; font-family: monospace; font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }

  .flow-kicker { color: #85857f; font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; letter-spacing: .1em; text-transform: uppercase; }
  .flow-diagram { margin-top: 40px; padding: 30px; display: grid; grid-template-columns: 1fr 44px 1fr 44px 1fr; gap: 0; align-items: center; border: 1px solid #292929; border-radius: 18px; background: #080808; }
  .flow-col { display: grid; gap: 10px; }
  .flow-node { min-height: 64px; padding: 14px 16px; display: flex; align-items: center; gap: 12px; border: 1px solid #2c2c2c; border-radius: 12px; background: #0e0e0e; color: #d6d6d2; font-size: 13.5px; line-height: 1.45; }
  .flow-node b { width: 28px; height: 28px; flex: 0 0 28px; display: grid; place-items: center; border: 1px solid #3a3a3a; border-radius: 9px; background: #171717; font-size: 12px; }
  .flow-node span { display: grid; gap: 3px; color: #8f8f8b; font-size: 12px; }
  .flow-node strong { color: #f2f2ef; font-size: 13.5px; font-weight: 600; }
  .flow-node-user { justify-content: flex-start; background: #161617; border-color: #333; }
  .flow-node-strong { border-color: #555; background: #121212; }
  .flow-arrow { height: 1px; margin: 0 8px; background: #3a3a3a; position: relative; }
  .flow-arrow::before { content: ""; position: absolute; top: -2px; left: 0; width: 5px; height: 5px; border-radius: 50%; background: #f2f2ef; box-shadow: 0 0 6px rgba(255,255,255,.6); animation: flowDot 2.4s cubic-bezier(.4,0,.2,1) infinite; }
  @keyframes flowDot { 0% { left: 0; opacity: 0; } 15% { opacity: 1; } 85% { opacity: 1; } 100% { left: calc(100% - 5px); opacity: 0; } }
  .flow-node .mini-face { width: 30px; height: 30px; flex: 0 0 30px; display: flex; align-items: center; justify-content: center; gap: 5px; border-radius: 9px; border: 1px solid #3a3a3a; background: #171717; }
  .mini-face i { width: 4px; height: 8px; border-radius: 4px; background: #f2f2ef; animation: miniBlink 5s infinite; }
  .flow-node .mini-face-echo { background: #f4f4f2; border-color: #f4f4f2; }
  .mini-face-echo i { background: #070707; animation-delay: 1.3s; }
  @keyframes miniBlink { 0%, 92%, 100% { transform: scaleY(1); } 95% { transform: scaleY(.1); } }
  .flow-node-working { animation: nodeWork 3.2s ease-in-out infinite; }
  .flow-node-late { animation-delay: 1.6s; }
  @keyframes nodeWork { 0%, 100% { border-color: #2c2c2c; } 50% { border-color: #5a5a57; box-shadow: 0 0 0 1px rgba(255,255,255,.04), 0 10px 30px rgba(0,0,0,.4); } }
  .flow-arrow::after { content: ""; position: absolute; right: 0; top: -3px; width: 6px; height: 6px; border-top: 1px solid #8a8a86; border-right: 1px solid #8a8a86; transform: rotate(45deg); }
  .flow-handoff { grid-column: 1 / -1; margin-top: 26px; padding-top: 22px; border-top: 1px solid #222; display: grid; grid-template-columns: 1fr 3fr; gap: 20px; align-items: start; }
  .flow-handoff p { margin: 0; color: #a1a19d; font-size: 14px; line-height: 1.65; }
  .flow-handoff strong { color: #f0f0ec; font-weight: 600; }
  .flow-handoff i { font-style: normal; color: #6f6f6c; padding: 0 2px; }



  .system-diagram { margin: 40px 0 0; padding: 26px; border: 1px solid #262626; border-radius: 20px; background: #080808; }
  .system-diagram svg { display: block; width: 100%; height: auto; }
  .sd-boundary { fill: none; stroke: #2e2e2e; stroke-width: 1.2; stroke-dasharray: 5 6; }
  .sd-node { fill: #0e0e0e; stroke: #2e2e2e; stroke-width: 1.2; }
  .sd-node-strong { fill: #131313; stroke: #5a5a57; }
  .sd-title { fill: #f2f2ef; font: 600 17px Inter, "Segoe UI", sans-serif; letter-spacing: -.01em; }
  .sd-sub { fill: #94948f; font: 400 13.5px Inter, "Segoe UI", sans-serif; }
  .sd-tag, .sd-kicker, .sd-label { fill: #77776f; font: 500 10px "SFMono-Regular", Consolas, monospace; letter-spacing: .12em; }
  .sd-kicker { fill: #8a8a86; }
  .sd-label { fill: #8a8a86; }
  .sd-note { fill: #8a8a86; font: 400 12.5px Inter, "Segoe UI", sans-serif; }
  .sd-chip { fill: #0c0c0c; stroke: #2a2a2a; stroke-width: 1.2; }
  .sd-chip-text { fill: #c8c8c4; font: 500 12.5px Inter, "Segoe UI", sans-serif; }
  .sd-line { fill: none; stroke: #4a4a47; stroke-width: 1.3; }
  .sd-arrowhead { fill: #8a8a86; }
  .system-stack { display: none; margin: 0; padding: 0; list-style: none; }
  .system-stack li { padding: 14px 16px; display: grid; gap: 4px; border: 1px solid #2c2c2c; border-radius: 12px; background: #0e0e0e; }
  .system-stack li strong { color: #f2f2ef; font-size: 14px; font-weight: 600; }
  .system-stack li span { color: #94948f; font-size: 12.5px; line-height: 1.5; }
  .system-stack li.is-strong { border-color: #555; background: #131313; }
  .system-stack li.is-link { position: relative; padding: 14px 0 14px 22px; border: 0; background: none; color: #8a8a86; font-family: monospace; font-size: 10px; letter-spacing: .1em; text-transform: uppercase; }
  .system-stack li.is-link::before { content: ""; position: absolute; left: 10px; top: 0; bottom: 0; width: 1px; background: #3a3a3a; }
  .timeline { margin: 16px 0 0; padding: 30px 26px 26px; list-style: none; display: grid; grid-template-columns: repeat(4, 1fr); gap: 28px; position: relative; border: 1px solid #262626; border-radius: 20px; background: #080808; }
  .timeline::before { content: ""; position: absolute; left: 44px; right: 44px; top: 47px; height: 1px; background: #333; }
  .timeline li { position: relative; display: grid; align-content: start; gap: 10px; }
  .timeline-dot { width: 36px; height: 36px; display: grid; place-items: center; border: 1px solid #4a4a47; border-radius: 50%; background: #0e0e0e; color: #e6e6e2; font: 500 11px "SFMono-Regular", Consolas, monospace; position: relative; z-index: 1; }
  .timeline h3 { margin: 10px 0 0; font-size: 19px; font-weight: 600; letter-spacing: -.02em; }
  .timeline p { margin: 0; color: #9a9a96; font-size: 13.5px; line-height: 1.6; }
  .download-section { margin-bottom: 60px; padding: 56px clamp(38px, 6vw, 96px); border-radius: 28px; display: grid; grid-template-columns: .7fr 1.3fr; gap: 64px; align-items: center; background: var(--white); color: #080808; overflow: hidden; }
  .download-mark { height: 340px; display: grid; place-items: center; position: relative; }
  .download-mark::before, .download-mark::after { content: ""; position: absolute; border: 1px solid rgba(0,0,0,.13); border-radius: 50%; }
  .download-mark::before { width: 310px; height: 310px; }
  .download-mark::after { width: 225px; height: 225px; }
  .download-face { position: relative; z-index: 2; width: 140px; height: 140px; border-radius: 39px; display: flex; align-items: center; justify-content: center; gap: 25px; background: #090909; box-shadow: 0 28px 60px rgba(0,0,0,.22); transform: rotate(-4deg); }
  .download-face i { width: 13px; height: 22px; border-radius: 10px; background: #f4f4f2; }
  .download-copy .section-index { color: #555; }
  .download-copy .section-index strong { color: #333; border-color: #aaa; }
  .download-copy h2 { margin: 18px 0 0; font-size: clamp(52px, 5.4vw, 80px); line-height: 1.015; letter-spacing: -.06em; font-weight: 560; }
  .download-copy > p { max-width: 660px; margin: 20px 0 0; color: #444441; font-size: 18px; line-height: 1.62; }
  .download-actions { margin-top: 28px; display: flex; flex-wrap: wrap; align-items: center; gap: 25px; }
  .button-light { border-color: #090909; background: #090909; color: #fff !important; }
  .button-light:hover { background: #1d1d1d; }
  .text-link { display: inline-flex; align-items: center; gap: 8px; color: #222 !important; font-size: 13px; font-weight: 600; }
  .text-link:hover { text-decoration: underline !important; text-underline-offset: 3px; }
  .download-note { display: block; margin-top: 20px; color: #5e5e5b; font-size: 10.5px; font-family: monospace; line-height: 1.6; }
  .requirements { margin: 12px 0 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px 28px; max-width: 760px; }
  .requirements div { display: grid; grid-template-columns: 64px 1fr; gap: 10px; color: #5e5e5b; font-family: monospace; font-size: 10.5px; line-height: 1.5; }
  .requirements dt { color: #333; text-transform: uppercase; letter-spacing: .06em; }
  .requirements dd { margin: 0; }

  .site-footer { min-height: 130px; padding-block: 36px; border-top: 1px solid var(--line); display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; color: #85857f; }
  .site-footer .wordmark { color: #d7d7d3; }
  .site-footer p { font-size: 12px; }
  .site-footer > div { justify-self: end; display: flex; gap: 22px; font-size: 12px; }

  @media (max-width: 1180px) {
    .section-shell { width: min(100% - 48px, 1040px); }
    .site-header { padding-inline: 24px; }
    .site-header nav { gap: 20px; }
    .hero { grid-template-columns: 1fr; padding-top: 120px; }
    .hero-copy { max-width: 720px; }
    .capability-grid { grid-template-columns: repeat(2, 1fr); }
        .flow-diagram { grid-template-columns: 1fr; gap: 14px; }
    .timeline { grid-template-columns: repeat(2, 1fr); }
    .timeline::before { display: none; }
    .flow-arrow { width: 1px; height: 22px; margin: 0 auto; }
    .flow-arrow::after { top: auto; bottom: 0; right: -3px; transform: rotate(135deg); }
    .flow-arrow::before { left: -2px; animation-name: flowDotDown; }
    @keyframes flowDotDown { 0% { top: 0; opacity: 0; } 15%, 85% { opacity: 1; } 100% { top: calc(100% - 5px); opacity: 0; } }
  }
  @media (max-width: 820px) {
    .site-header { grid-template-columns: 1fr auto; }
    .site-header nav { display: none; }
        .chapter-nav { grid-template-columns: repeat(2, 1fr); }
    .flow-handoff { grid-template-columns: 1fr; gap: 10px; }
    .sh-body { grid-template-columns: 1fr; align-items: start; }
    .sh-num { top: -40px; }
    .orbit-feature { grid-template-columns: 1fr; padding: 30px 24px; }
    .system-diagram { padding: 16px; }
    .system-diagram svg { display: none; }
    .system-stack { display: grid; }
    .timeline { grid-template-columns: 1fr; }
            .download-section { grid-template-columns: 1fr; gap: 20px; padding-block: 60px; }
    .download-mark { height: 260px; order: 2; }
    .requirements { grid-template-columns: 1fr; }
    .download-copy { order: 1; }
    .echo-stage { min-height: 480px; }
    .site-footer { grid-template-columns: 1fr auto; }
    .site-footer p { display: none; }
  }
  @media (max-width: 590px) {
    .section-shell { width: calc(100% - 32px); }
    .site-header { height: 64px; padding-inline: 16px; }
    .header-install { padding: 9px 12px; font-size: 11.5px; }
    .header-install svg { display: none; }
    .hero { padding-top: 100px; padding-bottom: 60px; gap: 40px; }
    .eyebrow { margin-bottom: 22px; font-size: 9.5px; }
    .hero h1 { font-size: clamp(46px, 14vw, 66px); }
    .button { width: 100%; }
    .capability-section { padding-block: 56px 28px; }
    .agents-section, .architecture-section { padding-block: 48px; }
    .capability-grid { grid-template-columns: 1fr; }
    .orbit-stage { height: 260px; transform: scale(.86); }
    .orbit-ring-c { display: none; }
    .capability-card { min-height: 0; }
    .chapter-nav a { padding: 20px 16px; grid-template-columns: 26px 1fr; min-height: 80px; }
    .chapter-nav i { left: 16px; right: 16px; bottom: 14px; }
    .flow-diagram { padding: 18px; }
        .echo-stage { padding: 22px; min-height: 420px; gap: 18px; }
    .echo-stage-face { margin-top: 6px; }
    .echo-stage-says { justify-self: center; margin-right: 0; }
    .echo-stage-team { display: none; }
    .download-section { width: calc(100% - 20px); margin-bottom: 30px; padding: 40px 24px; border-radius: 22px; }
    .download-mark::before { width: 230px; height: 230px; }
    .download-mark::after { width: 165px; height: 165px; }
    .download-face { width: 118px; height: 118px; border-radius: 31px; }
    .download-copy h2 { font-size: 55px; }
    .requirements div { grid-template-columns: 1fr; gap: 2px; }
      .site-footer { grid-template-columns: 1fr; gap: 24px; }
    .site-footer > div { justify-self: start; flex-wrap: wrap; }
  }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } .marketing-site *, .marketing-site *::before, .marketing-site *::after { animation: none !important; transition-duration: .01ms !important; } .sd-pulse { display: none; } }
`;
