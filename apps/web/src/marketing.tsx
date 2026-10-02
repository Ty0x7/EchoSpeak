import React from "react";
import { SquareAvatarVisual } from "./components/SquareAvatarVisual";

const githubUrl = import.meta.env.VITE_GITHUB_URL || "https://github.com/Ty0x7/EchoSpeak";
const installerUrl = import.meta.env.VITE_DESKTOP_DOWNLOAD_URL || `${githubUrl}/releases/latest`;

type IconName = "chat" | "research" | "code" | "memory" | "voice" | "connect" | "shield" | "windows" | "github" | "arrow";

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.65, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "chat") return <svg {...common}><path d="M5 18.5 3.5 21l.7-4A8.5 8.5 0 1 1 12 20.5H8" /><path d="M8 10h8M8 14h5" /></svg>;
  if (name === "research") return <svg {...common}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5M8 11h6M11 8v6" /></svg>;
  if (name === "code") return <svg {...common}><path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14" /></svg>;
  if (name === "memory") return <svg {...common}><path d="M7 4.5A3.5 3.5 0 0 0 5.5 11v2A3.5 3.5 0 0 0 7 19.5M17 4.5A3.5 3.5 0 0 1 18.5 11v2a3.5 3.5 0 0 1-1.5 6.5M9 4v16M15 4v16M9 8h6M9 16h6" /></svg>;
  if (name === "voice") return <svg {...common}><rect x="8" y="3" width="8" height="12" rx="4" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" /></svg>;
  if (name === "connect") return <svg {...common}><path d="M8.5 8.5 6 6a3 3 0 0 0-4 4l3 3a3 3 0 0 0 4 0l2-2M15.5 15.5 18 18a3 3 0 0 0 4-4l-3-3a3 3 0 0 0-4 0l-2 2M8 16l8-8" /></svg>;
  if (name === "shield") return <svg {...common}><path d="M12 3 4.5 6v5.5c0 4.4 3 7.7 7.5 9.5 4.5-1.8 7.5-5.1 7.5-9.5V6L12 3Z" /><path d="m9 12 2 2 4-4" /></svg>;
  if (name === "windows") return <svg {...common}><path d="M3 5.5 10.5 4v7H3v-5.5ZM13 3.5 21 2v9h-8V3.5ZM3 13h7.5v7L3 18.5V13ZM13 13h8v9l-8-1.5V13Z" /></svg>;
  if (name === "github") return <svg {...common}><path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3.3-.4 6.8-1.6 6.8-7.4A5.8 5.8 0 0 0 19.3 3a5.4 5.4 0 0 0-.2-4S17.9-1.4 15 1a14 14 0 0 0-6 0C6.1-1.4 4.9-1 4.9-1a5.4 5.4 0 0 0-.2 4A5.8 5.8 0 0 0 3.2 7.1c0 5.8 3.5 7 6.8 7.4A4.8 4.8 0 0 0 9 18v4M9 19c-3 .9-3-1.5-4.2-2" /></svg>;
  return <svg {...common}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
}

const capabilities: Array<{ icon: IconName; title: string; copy: string; meta: string }> = [
  { icon: "chat", title: "One continuous conversation", copy: "Talk naturally while Echo keeps multi-step work, tool activity, recovery, and the final answer together in one understandable run.", meta: "Chat + durable work" },
  { icon: "research", title: "Research with evidence", copy: "Search current information, inspect public sources, compare results, and keep provenance attached to the answer instead of treating a search result as proof.", meta: "Search + retrieval + verification" },
  { icon: "code", title: "Project-aware coding", copy: "Attach a local Project and let Echo read, reason about, and modify its files within explicit workspace and approval boundaries.", meta: "Files + terminal + specialists" },
  { icon: "memory", title: "Memory that stays useful", copy: "Echo can recall preferences, prior context, and Project knowledge without flooding every prompt or mixing unrelated work together.", meta: "Profile + Session + Project" },
  { icon: "voice", title: "A voice and a presence", copy: "Use dictation, spoken replies, and the optional desktop companion while sharing the exact same Session and runtime as the main application.", meta: "One Echo across surfaces" },
  { icon: "connect", title: "Tools you can inspect", copy: "Providers, skills, MCP servers, and connections expose their real availability, scope, and health before Echo relies on them.", meta: "Visible capability state" },
];

const principles = [
  ["01", "Understand", "The selected Session model interprets the request and separates independent requirements."],
  ["02", "Act", "The runtime chooses a capable tool or source and records every governed execution."],
  ["03", "Verify", "Results become evidence only when they cover the information that was actually requested."],
  ["04", "Respond", "One finalization boundary decides when the work is complete, partial, or needs you."],
];

const chapters = [
  ["01", "The idea", "#the-idea"],
  ["02", "Capabilities", "#capabilities"],
  ["03", "How it works", "#how-it-works"],
  ["04", "Built for real work", "#real-work"],
];

function ChapterLabel({ number, children }: { number: string; children: React.ReactNode }) {
  return <p className="section-index"><strong>{number}</strong><span>{children}</span></p>;
}

function EchoStage() {
  return (
    <div className="echo-stage" aria-label="Echo, the EchoSpeak companion">
      <div className="echo-stage-grid" aria-hidden="true" />
      <div className="echo-orbit echo-orbit-one" aria-hidden="true" />
      <div className="echo-orbit echo-orbit-two" aria-hidden="true" />
      <div className="echo-avatar-full">
        <SquareAvatarVisual
          speaking={false}
          backendOnline={true}
          heartbeatEnabled={false}
          avatarConfig={{
            body_color: "#f4f4f2",
            eye_color: "#050505",
            bg_color: "#070707",
            glow_color: "#ffffff",
            idle_activity: "auto",
            breathing_speed: 0.72,
            enable_glow: false,
            enable_idle_activities: true,
          }}
        />
      </div>
      <div className="echo-stage-label"><span>Echo</span><small>Local desktop presence</small></div>
      <div className="echo-stage-state"><i />Ready when you are</div>
    </div>
  );
}

export function Marketing() {
  return (
    <div className="marketing-site">
      <style>{styles}</style>

      <header className="site-header">
        <a className="wordmark" href="#top" aria-label="EchoSpeak home">
          <span className="wordmark-face"><span /><span /></span>
          <span>EchoSpeak</span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#capabilities">Capabilities</a>
          <a href="#how-it-works">How it works</a>
          <a href="#download">Install</a>
        </nav>
        <a className="header-install" href={installerUrl}>Install for Windows <Icon name="arrow" size={16} /></a>
      </header>

      <main id="top">
        <section className="hero section-shell">
          <div className="hero-copy">
            <p className="eyebrow"><span>EchoSpeak 8.0</span><span>Windows desktop</span></p>
            <h1>Your computer.<br />Your context.<br /><em>Your Echo.</em></h1>
            <p className="hero-lede">A local-first personal agent that can talk, research, remember, work across Projects, and use real tools—without hiding how the work gets done.</p>
            <div className="hero-actions">
              <a className="button button-primary" href={installerUrl}><Icon name="windows" />Install for Windows <Icon name="arrow" size={17} /></a>
              <a className="button button-secondary" href={githubUrl} target="_blank" rel="noreferrer"><Icon name="github" />View on GitHub</a>
            </div>
            <div className="hero-details">
              <span>Windows 10 / 11</span>
              <span>Local-first</span>
              <span>Bring your own model</span>
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

        <section className="manifesto section-shell" id="the-idea" aria-labelledby="manifesto-title">
          <div className="manifesto-heading">
            <ChapterLabel number="01">The idea</ChapterLabel>
            <div className="manifesto-copy">
              <h2 id="manifesto-title">Not another chat window.<br /><span>A durable agent for your desktop.</span></h2>
              <p>EchoSpeak keeps the conversation human while the runtime keeps the work exact. Sessions hold continuity. Projects define local scope. TaskRuns preserve objectives. ToolRuns record what truly executed. Evidence stays connected to the claim it supports.</p>
            </div>
          </div>
        </section>

        <section className="capability-section section-shell" id="capabilities" aria-label="EchoSpeak capabilities">
          <div className="section-heading">
            <ChapterLabel number="02">Capabilities</ChapterLabel>
            <span className="capability-count">06 capabilities <i /> 01 Echo</span>
          </div>
          <div className="capability-feature">
            <div className="capability-feature-copy">
              <span>One core / many ways to work</span>
              <h3>Echo changes modes without changing who he is.</h3>
              <p>Ask a question, start research, attach a Project, or speak aloud. The surface can change; the Session, memory, authority, and work history stay connected.</p>
            </div>
            <div className="capability-orbit" aria-hidden="true">
              <div className="orbit-ring orbit-ring-a" /><div className="orbit-ring orbit-ring-b" />
              <div className="orbit-core"><span className="idea-face"><i /><i /></span><b>Echo</b></div>
              <span className="orbit-label orbit-label-a">Chat</span><span className="orbit-label orbit-label-b">Research</span><span className="orbit-label orbit-label-c">Projects</span><span className="orbit-label orbit-label-d">Memory</span><span className="orbit-label orbit-label-e">Voice</span><span className="orbit-label orbit-label-f">Tools</span>
            </div>
          </div>
          <div className="capability-detail-panel">
            <div className="capability-detail-head"><span>Capability map</span><small>all connected to the same Session</small></div>
            <div className="capability-detail-list">
              {capabilities.map((capability, index) => (
                <div className="capability-detail-row" key={capability.title}>
                  <Icon name={capability.icon} size={19} />
                  <div><strong>{capability.title}</strong><span>{capability.meta}</span></div>
                  <b>{String(index + 1).padStart(2, "0")}</b>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="architecture-section section-shell" id="how-it-works" aria-labelledby="architecture-title">
          <div className="architecture-heading">
            <ChapterLabel number="03">How it works</ChapterLabel>
            <h2 id="architecture-title">Simple on the surface.<br />Accountable underneath.</h2>
          </div>
          <div className="runtime-line" aria-label="EchoSpeak request flow">
            {principles.map(([number, title, copy], index) => (
              <React.Fragment key={number}>
                <article className="runtime-step">
                  <span>{number}</span>
                  <div><h3>{title}</h3><p>{copy}</p></div>
                </article>
                {index < principles.length - 1 && <div className="runtime-connector" aria-hidden="true"><i /></div>}
              </React.Fragment>
            ))}
          </div>
          <div className="authority-strip">
            <div><Icon name="shield" size={28} /><strong>Authority stays visible.</strong></div>
            <p>Read-only research stays fast. Sensitive actions use exact permissions and approvals. The model proposes; the runtime validates; durable records preserve the truth.</p>
          </div>
        </section>

        <section className="product-story section-shell" id="real-work" aria-labelledby="product-story-title">
          <div className="story-copy">
            <ChapterLabel number="04">Built for real work</ChapterLabel>
            <h2 id="product-story-title">Conversation when it is simple.<br />Structure when it matters.</h2>
            <p>A quick question should feel quick. A multi-part objective should survive tool failures, approvals, corrections, restarts, and changing evidence without losing the parts already completed.</p>
          </div>
          <div className="work-ledger" aria-label="Conceptual EchoSpeak work ledger">
            <div className="ledger-header"><span>Current objective</span><small>Durable work</small></div>
            <h3>Compare options and prepare the Project</h3>
            <div className="ledger-row done"><i>01</i><span>Recall preferences</span><b>Complete</b></div>
            <div className="ledger-row active"><i>02</i><span>Research current options</span><b>In progress</b></div>
            <div className="ledger-row"><i>03</i><span>Verify the evidence</span><b>Pending</b></div>
            <div className="ledger-row"><i>04</i><span>Apply to Project</span><b>Approval</b></div>
            <div className="ledger-foot"><span>Completed work remains preserved</span><span>2 evidence records</span></div>
          </div>
        </section>

        <section className="download-section section-shell" id="download" aria-labelledby="download-title">
          <div className="download-mark" aria-hidden="true"><span className="download-face"><i /><i /></span></div>
          <div className="download-copy">
            <ChapterLabel number="05">Install</ChapterLabel>
            <h2 id="download-title">Bring Echo home.</h2>
            <p>Install the native Windows desktop application, connect the model provider you choose, and keep Echo close to the work that matters.</p>
            <div className="download-actions">
              <a className="button button-light" href={installerUrl}><Icon name="windows" />Download for Windows <Icon name="arrow" size={17} /></a>
              <a className="text-link" href={githubUrl} target="_blank" rel="noreferrer"><Icon name="github" size={18} />Source and releases on GitHub <Icon name="arrow" size={15} /></a>
            </div>
            <small className="download-note">EchoSpeak 8.0 · Windows x64 · Installer availability follows published GitHub releases.</small>
          </div>
        </section>
      </main>

      <footer className="site-footer section-shell">
        <a className="wordmark" href="#top"><span className="wordmark-face"><span /><span /></span><span>EchoSpeak</span></a>
        <p>Local-first personal agent runtime for Windows.</p>
        <div><a href={githubUrl} target="_blank" rel="noreferrer">GitHub</a><a href="#capabilities">Capabilities</a><a href="#download">Install</a></div>
      </footer>
    </div>
  );
}

const styles = `
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body { margin: 0; background: #050505; }
  .marketing-site { --black: #050505; --ink: #0a0a0a; --panel: #0d0d0d; --soft: #151515; --line: #272727; --line-soft: #191919; --white: #f4f4f2; --muted: #9a9a96; --dim: #666663; min-height: 100vh; overflow: hidden; background: var(--black); color: var(--white); font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }
  .marketing-site a { color: inherit; text-decoration: none; }
  .section-shell { width: min(1440px, calc(100% - 80px)); margin-inline: auto; }
  .site-header { position: fixed; inset: 0 0 auto; z-index: 100; height: 76px; padding: 0 max(40px, calc((100vw - 1440px) / 2)); display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; border-bottom: 1px solid rgba(255,255,255,.08); background: rgba(5,5,5,.84); backdrop-filter: blur(20px); }
  .wordmark { display: inline-flex; align-items: center; gap: 11px; width: max-content; font-weight: 650; letter-spacing: -.025em; font-size: 17px; }
  .wordmark-face { width: 25px; height: 25px; display: flex; align-items: center; justify-content: center; gap: 4px; border-radius: 7px; background: var(--white); box-shadow: inset 0 0 0 1px #fff; }
  .wordmark-face span { width: 3px; height: 5px; border-radius: 3px; background: var(--black); }
  .site-header nav { display: flex; align-items: center; gap: 34px; color: #ababaa; font-size: 13px; }
  .site-header nav a, .site-footer a { transition: color .2s ease; }
  .site-header nav a:hover, .site-footer a:hover { color: #fff; }
  .header-install { justify-self: end; display: inline-flex; align-items: center; gap: 10px; padding: 11px 15px; border: 1px solid #393939; border-radius: 9px; font-size: 13px; font-weight: 600; background: #101010; transition: background .2s ease, border-color .2s ease; }
  .header-install:hover { background: #1a1a1a; border-color: #666; }
  .hero { min-height: 850px; padding-top: 150px; padding-bottom: 80px; display: grid; grid-template-columns: minmax(0, .96fr) minmax(460px, .92fr); gap: clamp(48px, 8vw, 132px); align-items: center; }
  .hero-copy { position: relative; z-index: 2; }
  .eyebrow { margin: 0 0 30px; display: flex; gap: 16px; align-items: center; color: #b8b8b5; font-family: "SFMono-Regular", Consolas, monospace; font-size: 11px; letter-spacing: .11em; text-transform: uppercase; }
  .eyebrow span + span { padding-left: 16px; border-left: 1px solid #393939; color: #6f6f6c; }
  .hero h1 { margin: 0; font-size: clamp(64px, 7.4vw, 116px); line-height: .91; letter-spacing: -.071em; font-weight: 620; }
  .hero h1 em { color: #777774; font-style: normal; }
  .hero-lede { max-width: 650px; margin: 35px 0 0; color: #aaa9a5; font-size: clamp(17px, 1.35vw, 21px); line-height: 1.58; letter-spacing: -.015em; }
  .hero-actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 42px; }
  .button { min-height: 54px; padding: 0 20px; display: inline-flex; align-items: center; justify-content: center; gap: 11px; border: 1px solid #333; border-radius: 11px; font-size: 14px; font-weight: 650; transition: transform .2s ease, background .2s ease, border-color .2s ease; }
  .button:hover { transform: translateY(-2px); }
  .button-primary { background: var(--white); color: #070707 !important; border-color: var(--white); }
  .button-primary:hover { background: #fff; }
  .button-secondary { background: #0b0b0b; color: #dededb; }
  .button-secondary:hover { background: #141414; border-color: #5b5b5b; }
  .hero-details { display: flex; flex-wrap: wrap; gap: 20px; margin-top: 25px; color: #686865; font-family: "SFMono-Regular", Consolas, monospace; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
  .hero-details span { display: inline-flex; align-items: center; gap: 8px; }
  .hero-details span::before { content: ""; width: 3px; height: 3px; border-radius: 50%; background: #898985; }
  .echo-stage { position: relative; width: 100%; min-height: 610px; border: 1px solid #202020; border-radius: 28px; overflow: hidden; background: radial-gradient(circle at 50% 48%, #171717 0%, #0a0a0a 40%, #070707 72%); isolation: isolate; }
  .echo-stage::after { content: ""; position: absolute; inset: auto 7% 14%; height: 1px; background: linear-gradient(90deg, transparent, #353535, transparent); }
  .echo-stage-grid { position: absolute; inset: 0; opacity: .22; background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px); background-size: 44px 44px; mask-image: radial-gradient(circle at center, black, transparent 70%); }
  .echo-orbit { position: absolute; top: 50%; left: 50%; border: 1px solid rgba(255,255,255,.08); border-radius: 50%; transform: translate(-50%, -50%); }
  .echo-orbit-one { width: 410px; height: 410px; animation: orbitPulse 5s ease-in-out infinite; }
  .echo-orbit-two { width: 520px; height: 520px; opacity: .45; }
  .echo-avatar-full { position: absolute; inset: 66px 40px 80px; min-height: 430px; z-index: 3; overflow: visible; }
  .echo-stage-label { position: absolute; z-index: 5; bottom: 25px; left: 27px; display: flex; flex-direction: column; gap: 4px; }
  .echo-stage-label span { font-size: 17px; font-weight: 650; }
  .echo-stage-label small, .echo-stage-state { color: #777774; font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; letter-spacing: .09em; text-transform: uppercase; }
  .echo-stage-state { position: absolute; z-index: 5; right: 27px; bottom: 31px; display: flex; align-items: center; gap: 8px; }
  .echo-stage-state i { width: 6px; height: 6px; border-radius: 50%; background: #dededb; box-shadow: 0 0 10px rgba(255,255,255,.4); }
  @keyframes orbitPulse { 0%,100% { transform: translate(-50%,-50%) scale(.97); opacity: .55; } 50% { transform: translate(-50%,-50%) scale(1.03); opacity: .95; } }
  .chapter-nav { height: 112px; display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
  .chapter-nav a { position: relative; padding: 25px 24px 22px; display: grid; grid-template-columns: 34px 1fr; gap: 8px; align-items: start; color: #777774; border-right: 1px solid var(--line); transition: color .2s ease, background .2s ease; overflow: hidden; }
  .chapter-nav a:first-child { border-left: 1px solid var(--line); }
  .chapter-nav a:hover { color: #f3f3f0; background: #0a0a0a; }
  .chapter-nav span { color: #565653; font-family: monospace; font-size: 10px; }
  .chapter-nav strong { font-size: 12px; font-weight: 540; letter-spacing: -.01em; }
  .chapter-nav i { position: absolute; left: 24px; right: 24px; bottom: 20px; height: 1px; background: #272727; }
  .chapter-nav i::after { content: ""; display: block; width: 0; height: 1px; background: #e7e7e3; transition: width .3s ease; }
  .chapter-nav a:hover i::after { width: 100%; }
  .section-index { margin: 0; min-width: 156px; display: inline-flex; align-items: center; gap: 12px; color: #747471; font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; letter-spacing: .11em; text-transform: uppercase; }
  .section-index strong { width: 31px; height: 31px; display: grid; place-items: center; flex: 0 0 auto; border: 1px solid #333; border-radius: 50%; color: #d3d3cf; font-size: 9px; font-weight: 500; }
  .section-index span { white-space: nowrap; }
  .manifesto { padding-block: 145px; }
  .manifesto-heading { display: grid; grid-template-columns: 1fr 3fr; gap: 40px; align-items: start; }
  .manifesto h2, .architecture-heading h2, .story-copy h2, .download-copy h2 { margin: 0; font-size: clamp(45px, 5vw, 73px); line-height: 1.015; letter-spacing: -.06em; font-weight: 560; }
  .manifesto h2 span { color: #70706d; }
  .manifesto-copy { max-width: 980px; }
  .manifesto-copy > p { max-width: 850px; margin: 38px 0 0; color: #a1a19d; font-size: clamp(18px, 1.45vw, 22px); line-height: 1.62; letter-spacing: -.02em; }
  .idea-map { position: relative; min-height: 285px; margin-top: 68px; padding: 20px 22px; border: 1px solid #292929; border-radius: 17px; background: radial-gradient(circle at 50% 45%, #111 0%, #080808 56%); overflow: hidden; }
  .idea-map::before { content: ""; position: absolute; inset: 42px 0 88px; background: linear-gradient(90deg, transparent, rgba(255,255,255,.055), transparent); opacity: .6; }
  .idea-map-top { position: relative; z-index: 1; display: flex; justify-content: space-between; color: #b6b6b2; font-size: 11px; font-family: monospace; letter-spacing: .08em; text-transform: uppercase; }
  .idea-map-top small { color: #555552; font-size: 9px; }
  .idea-map-core { position: relative; z-index: 2; width: 116px; height: 116px; margin: 22px auto 24px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 1px solid #51514e; border-radius: 50%; background: #121212; box-shadow: 0 0 0 11px rgba(255,255,255,.025), 0 0 0 22px rgba(255,255,255,.015); }
  .idea-map-core::before, .idea-map-core::after { content: ""; position: absolute; top: 50%; width: 145px; height: 1px; background: #262626; }
  .idea-map-core::before { right: 100%; }
  .idea-map-core::after { left: 100%; }
  .idea-map-core strong { margin-top: 6px; font-size: 14px; font-weight: 600; }
  .idea-map-core small { margin-top: 4px; color: #666663; font-size: 8px; font-family: monospace; }
  .idea-face { width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; gap: 5px; border-radius: 9px; background: #f0f0ed; }
  .idea-face i { width: 4px; height: 7px; border-radius: 5px; background: #090909; }
  .idea-map-lanes { position: relative; z-index: 2; display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid #292929; }
  .idea-map-lanes > div { min-height: 68px; padding: 15px 15px 0 0; display: grid; grid-template-columns: 26px 1fr; align-items: start; column-gap: 8px; position: relative; }
  .idea-map-lanes > div + div { padding-left: 18px; border-left: 1px solid #292929; }
  .idea-map-lanes .lane-number { color: #555552; font-family: monospace; font-size: 9px; }
  .idea-map-lanes strong { font-size: 13px; font-weight: 560; }
  .idea-map-lanes small { grid-column: 2; margin-top: 4px; color: #777774; font-size: 10px; }
  .idea-map-lanes i { position: absolute; right: 12px; top: -4px; width: 7px; height: 7px; border: 1px solid #777; border-radius: 50%; background: #0b0b0b; }
  .capability-section { padding-block: 130px; border-top: 1px solid var(--line); }
  .section-heading { display: grid; grid-template-columns: 1fr auto; gap: 40px; align-items: start; }
  .capability-count { justify-self: end; margin-top: 10px; display: inline-flex; align-items: center; gap: 12px; color: #777774; font-family: monospace; font-size: 9px; letter-spacing: .1em; text-transform: uppercase; }
  .capability-count i { width: 20px; height: 1px; background: #454542; }
  .capability-feature { min-height: 330px; margin-top: 72px; padding: 37px 42px; display: grid; grid-template-columns: 1fr .9fr; gap: 25px; align-items: center; border: 1px solid #2d2d2d; border-radius: 17px; background: linear-gradient(105deg, #111 0%, #0b0b0b 62%); overflow: hidden; }
  .capability-feature-copy > span { color: #666663; font-family: monospace; font-size: 9px; letter-spacing: .1em; text-transform: uppercase; }
  .capability-feature-copy h3 { max-width: 560px; margin: 19px 0 17px; font-size: clamp(28px, 3.2vw, 47px); line-height: 1.03; letter-spacing: -.05em; font-weight: 560; }
  .capability-feature-copy p { max-width: 510px; margin: 0; color: #898986; font-size: 14px; line-height: 1.65; }
  .capability-orbit { position: relative; width: 290px; height: 240px; justify-self: end; }
  .orbit-ring { position: absolute; top: 50%; left: 50%; border: 1px solid #30302f; border-radius: 50%; transform: translate(-50%, -50%) rotate(-18deg); }
  .orbit-ring-a { width: 205px; height: 100px; }
  .orbit-ring-b { width: 260px; height: 140px; transform: translate(-50%, -50%) rotate(33deg); opacity: .7; }
  .orbit-core { position: absolute; top: 50%; left: 50%; width: 88px; height: 88px; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 5px; border: 1px solid #555; border-radius: 50%; background: #161616; transform: translate(-50%, -50%); box-shadow: 0 0 0 10px rgba(255,255,255,.025); }
  .orbit-core .idea-face { width: 24px; height: 24px; border-radius: 7px; }
  .orbit-core .idea-face i { width: 3px; height: 6px; }
  .orbit-core b { font-size: 10px; font-weight: 600; }
  .orbit-label { position: absolute; padding: 5px 8px; border: 1px solid #30302f; border-radius: 5px; background: #0d0d0d; color: #a4a4a1; font-family: monospace; font-size: 8px; letter-spacing: .06em; text-transform: uppercase; }
  .orbit-label-a { top: 8px; left: 105px; } .orbit-label-b { top: 48px; right: 3px; } .orbit-label-c { bottom: 25px; right: 22px; } .orbit-label-d { bottom: 0; left: 105px; } .orbit-label-e { bottom: 37px; left: 10px; } .orbit-label-f { top: 42px; left: 0; }
  .capability-detail-panel { margin-top: 12px; padding: 27px 34px 31px; border: 1px solid #2d2d2d; border-radius: 17px; background: #0a0a0a; }
  .capability-detail-head { padding-bottom: 19px; display: flex; justify-content: space-between; border-bottom: 1px solid #272727; color: #c0c0bc; font-family: monospace; font-size: 9px; letter-spacing: .1em; text-transform: uppercase; }
  .capability-detail-head small { color: #565653; font: inherit; }
  .capability-detail-list { display: grid; grid-template-columns: repeat(2, 1fr); column-gap: 45px; }
  .capability-detail-row { min-height: 70px; display: grid; grid-template-columns: 28px 1fr 24px; gap: 12px; align-items: center; border-bottom: 1px solid #242424; color: #c4c4c0; }
  .capability-detail-row:nth-last-child(-n+2) { border-bottom: 0; }
  .capability-detail-row > svg { color: #a2a29e; }
  .capability-detail-row div { display: flex; flex-direction: column; gap: 4px; }
  .capability-detail-row strong { font-size: 13px; font-weight: 560; }
  .capability-detail-row span { color: #676764; font-family: monospace; font-size: 8px; letter-spacing: .07em; text-transform: uppercase; }
  .capability-detail-row b { justify-self: end; color: #555552; font-family: monospace; font-size: 9px; font-weight: 400; }
  .architecture-section { padding-block: 140px; border-top: 1px solid var(--line); }
  .architecture-heading { display: grid; grid-template-columns: 1fr 3fr; gap: 40px; align-items: start; }
  .architecture-heading h2 { max-width: 900px; }
  .runtime-line { position: relative; margin-top: 90px; padding: 18px; display: grid; grid-template-columns: 1fr 30px 1fr 30px 1fr 30px 1fr; align-items: stretch; border: 1px solid #292929; border-radius: 20px; background: #080808; }
  .runtime-line::before { content: "One continuous runtime"; position: absolute; top: -25px; right: 0; color: #575754; font-family: monospace; font-size: 9px; letter-spacing: .09em; text-transform: uppercase; }
  .runtime-step { min-height: 265px; padding: 24px; border: 0; border-radius: 13px; display: flex; flex-direction: column; background: transparent; transition: background .2s ease; }
  .runtime-step:hover { background: #101010; }
  .runtime-step > span { color: #4f4f4d; font-family: monospace; font-size: 42px; line-height: 1; letter-spacing: -.08em; }
  .runtime-step div { margin-top: auto; }
  .runtime-step h3 { margin: 0 0 12px; font-size: 24px; letter-spacing: -.035em; font-weight: 580; }
  .runtime-step p { margin: 0; color: #898986; font-size: 13px; line-height: 1.58; }
  .runtime-connector { display: flex; align-items: center; }
  .runtime-connector i { width: 100%; height: 1px; background: #353535; position: relative; }
  .runtime-connector i::after { content: ""; position: absolute; top: -3px; right: 0; width: 6px; height: 6px; border-top: 1px solid #777; border-right: 1px solid #777; transform: rotate(45deg); }
  .authority-strip { margin-top: 14px; padding: 28px 31px; border: 1px solid #292929; border-radius: 14px; display: grid; grid-template-columns: .85fr 1.7fr; gap: 40px; align-items: center; background: #0b0b0b; }
  .authority-strip div { display: flex; align-items: center; gap: 16px; }
  .authority-strip strong { font-size: 16px; }
  .authority-strip p { margin: 0; color: #8e8e8a; font-size: 13px; line-height: 1.6; }
  .product-story { padding-block: 145px; border-top: 1px solid var(--line); display: grid; grid-template-columns: .85fr 1.15fr; gap: clamp(60px, 9vw, 140px); align-items: center; }
  .story-copy h2 { margin-top: 25px; font-size: clamp(40px, 4.4vw, 67px); }
  .story-copy > p:last-child { margin: 30px 0 0; max-width: 600px; color: #949491; font-size: 16px; line-height: 1.72; }
  .work-ledger { position: relative; border: 1px solid #303030; border-radius: 18px; overflow: hidden; background: #0c0c0c; box-shadow: 0 38px 90px rgba(0,0,0,.35); }
  .work-ledger::before { content: ""; position: absolute; inset: 0; pointer-events: none; background: linear-gradient(110deg, transparent 25%, rgba(255,255,255,.022) 50%, transparent 75%); }
  .ledger-header { padding: 20px 24px 0; display: flex; justify-content: space-between; color: #777774; font-family: monospace; font-size: 9px; letter-spacing: .1em; text-transform: uppercase; }
  .ledger-header small { font: inherit; }
  .work-ledger > h3 { margin: 12px 24px 25px; max-width: 480px; font-size: 22px; line-height: 1.25; letter-spacing: -.025em; }
  .ledger-row { min-height: 65px; padding: 0 24px; border-top: 1px solid #242424; display: grid; grid-template-columns: 44px 1fr auto; align-items: center; color: #868683; font-size: 13px; }
  .ledger-row i { font-style: normal; font-family: monospace; color: #51514f; }
  .ledger-row b { font-size: 10px; font-weight: 500; font-family: monospace; text-transform: uppercase; letter-spacing: .06em; }
  .ledger-row.done { color: #c1c1bd; }
  .ledger-row.active { color: #fff; background: #141414; box-shadow: inset 2px 0 #efefec; }
  .ledger-foot { padding: 18px 24px; border-top: 1px solid #242424; display: flex; justify-content: space-between; color: #5e5e5b; font-family: monospace; font-size: 9px; text-transform: uppercase; letter-spacing: .06em; }
  .download-section { min-height: 620px; margin-bottom: 60px; padding: 80px clamp(38px, 7vw, 105px); border-radius: 28px; display: grid; grid-template-columns: .75fr 1.25fr; gap: 80px; align-items: center; background: var(--white); color: #080808; overflow: hidden; }
  .download-mark { height: 390px; display: grid; place-items: center; position: relative; }
  .download-mark::before, .download-mark::after { content: ""; position: absolute; border: 1px solid rgba(0,0,0,.13); border-radius: 50%; }
  .download-mark::before { width: 340px; height: 340px; }
  .download-mark::after { width: 245px; height: 245px; }
  .download-face { position: relative; z-index: 2; width: 148px; height: 148px; border-radius: 39px; display: flex; align-items: center; justify-content: center; gap: 25px; background: #090909; box-shadow: 0 28px 60px rgba(0,0,0,.22); transform: rotate(-4deg); }
  .download-face i { width: 13px; height: 22px; border-radius: 10px; background: #f4f4f2; }
  .download-copy .section-index { color: #666; }
  .download-copy .section-index strong { color: #333; border-color: #aaa; }
  .download-copy h2 { margin-top: 21px; font-size: clamp(56px, 6vw, 88px); }
  .download-copy > p { max-width: 660px; margin: 26px 0 0; color: #4e4e4b; font-size: 18px; line-height: 1.62; }
  .download-actions { margin-top: 36px; display: flex; flex-wrap: wrap; align-items: center; gap: 25px; }
  .button-light { border-color: #090909; background: #090909; color: #fff !important; }
  .button-light:hover { background: #1d1d1d; }
  .text-link { display: inline-flex; align-items: center; gap: 9px; color: #272727 !important; font-size: 13px; font-weight: 600; }
  .download-note { display: block; margin-top: 24px; color: #777; font-size: 10px; font-family: monospace; }
  .site-footer { min-height: 150px; padding-block: 40px; border-top: 1px solid var(--line); display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; color: #6f6f6c; }
  .site-footer .wordmark { color: #d7d7d3; }
  .site-footer p { font-size: 11px; }
  .site-footer > div { justify-self: end; display: flex; gap: 24px; font-size: 11px; }
  @media (max-width: 1120px) {
    .section-shell { width: min(100% - 48px, 1040px); }
    .site-header { padding-inline: 24px; }
    .hero { grid-template-columns: 1fr .85fr; gap: 45px; }
    .hero h1 { font-size: clamp(60px, 8vw, 86px); }
    .echo-stage { min-height: 540px; }
    .echo-avatar-full { inset: 52px 15px 75px; }
    .section-heading { grid-template-columns: .7fr auto; }
    .runtime-line { grid-template-columns: repeat(2, 1fr); gap: 4px; }
    .runtime-connector { display: none; }
  }
  @media (max-width: 820px) {
    .site-header { grid-template-columns: 1fr auto; }
    .site-header nav { display: none; }
    .hero { min-height: auto; padding-top: 130px; grid-template-columns: 1fr; }
    .hero-copy { max-width: 700px; }
    .echo-stage { width: 100%; min-height: 560px; }
    .manifesto-heading, .architecture-heading, .product-story, .download-section { grid-template-columns: 1fr; }
    .manifesto-heading { gap: 30px; }
    .chapter-nav { height: auto; grid-template-columns: repeat(2, 1fr); }
    .chapter-nav a { min-height: 90px; border-bottom: 1px solid var(--line); }
    .section-heading { grid-template-columns: 1fr; gap: 18px; }
    .capability-count { justify-self: start; margin-top: 0; }
    .product-story { gap: 65px; }
    .capability-feature { grid-template-columns: 1fr; gap: 20px; }
    .capability-orbit { justify-self: center; }
    .download-section { gap: 20px; padding-block: 60px; }
    .download-mark { height: 300px; order: 2; }
    .download-copy { order: 1; }
    .site-footer { grid-template-columns: 1fr auto; }
    .site-footer p { display: none; }
  }
  @media (max-width: 590px) {
    .section-shell { width: calc(100% - 28px); }
    .site-header { height: 68px; padding-inline: 14px; }
    .header-install { padding: 10px 12px; font-size: 11px; }
    .header-install svg { display: none; }
    .hero { padding-top: 110px; padding-bottom: 62px; gap: 55px; }
    .eyebrow { margin-bottom: 24px; font-size: 9px; }
    .hero h1 { font-size: clamp(53px, 16.2vw, 76px); }
    .hero-lede { font-size: 16px; }
    .button { width: 100%; }
    .hero-details { gap: 12px 17px; }
    .echo-stage { min-height: 440px; border-radius: 20px; }
    .echo-avatar-full { inset: 28px 5px 65px; }
    .echo-orbit-one { width: 310px; height: 310px; }
    .echo-orbit-two { width: 400px; height: 400px; }
    .echo-stage-state { display: none; }
    .manifesto, .capability-section, .architecture-section, .product-story { padding-block: 90px; }
    .manifesto h2, .architecture-heading h2, .story-copy h2 { font-size: 41px; }
    .manifesto-copy > p { font-size: 17px; }
    .chapter-nav { grid-template-columns: 1fr 1fr; }
    .chapter-nav a { padding: 20px 16px; grid-template-columns: 26px 1fr; }
    .chapter-nav i { left: 16px; right: 16px; bottom: 15px; }
    .idea-map { min-height: 390px; margin-top: 50px; }
    .idea-map-core { margin-top: 53px; margin-bottom: 59px; }
    .idea-map-core::before, .idea-map-core::after { width: 45px; }
    .idea-map-lanes { grid-template-columns: 1fr; }
    .idea-map-lanes > div, .idea-map-lanes > div + div { min-height: 63px; padding: 13px 0 0; border-left: 0; border-bottom: 1px solid #292929; }
    .idea-map-lanes > div:last-child { border-bottom: 0; }
    .idea-map-lanes i { top: 20px; right: 2px; }
    .capability-feature { margin-top: 52px; padding: 29px 22px 16px; }
    .capability-orbit { width: 270px; transform: scale(.9); transform-origin: center top; margin-bottom: -15px; }
    .capability-detail-panel { padding: 22px 17px; }
    .capability-detail-head { gap: 15px; flex-direction: column; }
    .capability-detail-list { grid-template-columns: 1fr; }
    .capability-detail-row:nth-last-child(-n+2) { border-bottom: 1px solid #242424; }
    .capability-detail-row:last-child { border-bottom: 0; }
    .runtime-line { grid-template-columns: 1fr; margin-top: 72px; padding: 10px; }
    .runtime-step { min-height: 210px; }
    .authority-strip { grid-template-columns: 1fr; gap: 20px; }
    .ledger-row { padding-inline: 17px; grid-template-columns: 34px 1fr auto; }
    .ledger-row b { font-size: 8px; }
    .ledger-foot { flex-direction: column; gap: 8px; }
    .download-section { width: calc(100% - 20px); margin-bottom: 30px; padding: 48px 26px; border-radius: 22px; }
    .download-mark::before { width: 260px; height: 260px; }
    .download-mark::after { width: 185px; height: 185px; }
    .download-face { width: 118px; height: 118px; border-radius: 31px; }
    .download-copy h2 { font-size: 55px; }
    .download-copy > p { font-size: 16px; }
    .site-footer { min-height: 180px; padding-block: 35px; grid-template-columns: 1fr; gap: 30px; }
    .site-footer > div { justify-self: start; flex-wrap: wrap; }
  }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; } }
`;
