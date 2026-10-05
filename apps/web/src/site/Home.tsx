import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EchoFace, echoFaceStyles } from "../components/EchoFace";
import { ChartView } from "../widgets/Chart";
import { WeatherCard } from "../widgets/Weather";
import "../widgets/widgets.css";
import { DownloadButton, Face, Icon, SiteFooter, SiteHeader, useScrollTo, type IconName } from "./Chrome";
import { GITHUB_URL, useLatestRelease } from "./release";
import "./site.css";
import { CreationsStory, ResearchStory, SetupStory, MotionControls, CreationImage } from "./Capabilities";

/** Adds data-visible once the element scrolls into view (immediately with reduced motion). */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "0px 0px -10% 0px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { ref, visible };
}

type Anim = "up" | "left" | "right" | "zoom" | "blur" | "flip";

function Reveal({ children, className = "", as: Tag = "div", id, anim = "up", delay = 0 }: { children: React.ReactNode; className?: string; as?: "div" | "section"; id?: string; anim?: Anim; delay?: number }) {
  const { ref, visible } = useReveal<HTMLDivElement>();
  return (
    <Tag ref={ref as any} id={id} className={`reveal ${className}`} data-anim={anim} data-visible={visible ? "true" : "false"} style={delay ? { transitionDelay: `${delay}ms` } : undefined}>
      {children}
    </Tag>
  );
}

// ── Hero ─────────────────────────────────────────────────────────────

function Hero() {
  const scrollTo = useScrollTo();
  return (
    <section className="hero shell">
      <div className="hero-copy">
        <p className="hero-eyebrow"><span aria-hidden="true" /> Your assistant. Your ideas. In motion.</p>
        <h1>Meet <span className="hl">Echo</span>, the AI that lives on your computer.</h1>
        <p className="hero-lede">Talk, research, build, and create with an AI that feels at home on your computer. One conversation. So many places to take it.</p>
        <div className="hero-actions">
          <DownloadButton />
          <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer"><Icon name="github" size={18} /> View on GitHub</a>
        </div>
        <ul className="hero-values">
          <li><Icon name="github" size={15} /> Open source</li>
          <li><Icon name="house" size={15} /> Chats saved on your PC</li>
          <li><Icon name="model" size={15} /> Local or cloud models</li>
        </ul>
      </div>
      <div className="hero-face">
        <div className="hero-character"><EchoFace size="clamp(180px, 24vw, 300px)" /></div>
      </div>
      <button className="hero-explore" type="button" onClick={() => scrollTo("about")}>
        Meet your new teammate <Icon name="arrow" size={16} />
      </button>
    </section>
  );
}

// ── One Echo, many ways ─────────────────────────────────────────────

type Mode = { id: string; icon: IconName; label: string; says: string; caption: string };
const MODES: Mode[] = [
  { id: "chat", icon: "chat", label: "Talk it through", says: "Ask me anything.", caption: "Plans, advice, explanations: talk to Echo like a person." },
  { id: "see", icon: "chart", label: "Answers you can see", says: "Here's the week.", caption: "Weather, stocks, scores and more show up as cards, not walls of text." },
  { id: "research", icon: "research", label: "Look things up", says: "I read the sources.", caption: "Echo searches the web, reads the pages and shows where answers came from." },
  { id: "create", icon: "spark", label: "Create something", says: "From a prompt to a picture.", caption: "Generate images and videos with a local or cloud provider, then find them in Creations." },
  { id: "build", icon: "code", label: "Build things", says: "Shipped it.", caption: "Echo edits your files, runs the tests and tells you what changed." },
  { id: "team", icon: "team", label: "Bring the team", says: "Glados is on it.", caption: "Echo hands work to Jarvis and Glados and tells you when it's really done." },
  { id: "voice", icon: "voice", label: "Just say “Hey Echo”", says: "I'm listening.", caption: "Turn on Wake and just talk. Speech stays on your PC." },
];

const DEMO_WEATHER = {
  location: "Denver, Colorado",
  units: "F" as const,
  current: { temp: 64, feels: 62, code: 1, humidity: 31, wind: 7, wind_unit: "mph" },
  hourly: [],
  daily: [
    { date: "2026-10-05", max: 71, min: 46, code: 0, precip: 0 },
    { date: "2026-10-06", max: 68, min: 44, code: 2, precip: 10 },
    { date: "2026-10-07", max: 59, min: 40, code: 61, precip: 60 },
    { date: "2026-10-08", max: 63, min: 41, code: 3, precip: 20 },
    { date: "2026-10-09", max: 70, min: 45, code: 0, precip: 0 },
  ],
  source: "Example",
};

type Step = { "--i": number };

/** Your message: a bubble on the right. */
function You({ children, i = 0 }: { children: React.ReactNode; i?: number }) {
  return <p className="cm cm-you" style={{ "--i": i } as React.CSSProperties & Step}>{children}</p>;
}

/** An agent's reply: name header on the left, text underneath, like the app. */
function Agent({ children, name = "Echo", tone = "light", i = 0 }: { children: React.ReactNode; name?: string; tone?: "light" | "dark"; i?: number }) {
  return (
    <div className="cm cm-agent" style={{ "--i": i } as React.CSSProperties & Step}>
      <div className="cm-head"><Face tone={tone} size={20} /><b>{name}</b><time>now</time></div>
      <div className="cm-body">{children}</div>
    </div>
  );
}

function Screen({ mode }: { mode: string }) {
  switch (mode) {
    case "chat":
      return (
        <div className="thread">
          <You>Plan a cozy Saturday for two. Not too expensive.</You>
          <Agent i={1}>
            <p>Here's a relaxed day for under $60:</p>
            <ul>
              <li>Morning coffee and a long walk somewhere green</li>
              <li>A free museum or gallery in the afternoon</li>
              <li>Dinner from a local market, picnic style</li>
            </ul>
            <p className="muted">Want me to check the weather first?</p>
          </Agent>
        </div>
      );
    case "see":
      return (
        <div className="thread">
          <You>What's the weather in Denver this week?</You>
          <Agent i={1}>
            <WeatherCard data={DEMO_WEATHER} />
          </Agent>
        </div>
      );
    case "research":
      return (
        <div className="thread">
          <You>Is an air fryer actually healthier than an oven?</You>
          <Agent i={1}>
            <p>Mostly the same food with less oil. Air fryers cook with hot moving air, so you get crispy results from a spoonful of oil instead of a pan of it.</p>
            <div className="chips">
              {["healthline.com", "mayoclinic.org", "seriouseats.com"].map((src, n) => <span key={src} className="chip"><b>{n + 1}</b>{src}</span>)}
            </div>
          </Agent>
        </div>
      );
    case "create":
      return <div className="thread"><You>Create a cinematic image of an astronaut exploring another world.</You><Agent i={1}><p>A new world, ready to keep in Creations.</p><div className="ways-creation"><CreationImage /><span>Sample image · Creations preview</span></div></Agent></div>;
    case "build":
      return (
        <div className="thread">
          <You>Add a dark mode toggle to my website and test it.</You>
          <Agent i={1}>
            <div className="steps-mini">
              <span><Icon name="check" size={12} /> Read <code>index.html</code> and <code>styles.css</code></span>
              <span><Icon name="check" size={12} /> Edited <code>styles.css</code> (+24 lines)</span>
              <span><Icon name="check" size={12} /> Ran the tests: 12 passed</span>
            </div>
            <p>Done. The toggle sits in the header and remembers your choice.</p>
          </Agent>
        </div>
      );
    case "team":
      return (
        <div className="thread">
          <You>@Echo get Jarvis to find a quick pasta recipe and have Glados save it to recipes.txt</You>
          <Agent i={1}><p>On it. Jarvis, can you find a good one?</p></Agent>
          <Agent i={2} name="Jarvis" tone="dark"><p>Found a 20-minute garlic pasta (3 sources).</p></Agent>
          <Agent i={3} name="Glados" tone="dark"><p>Saved to <code>recipes.txt</code>.</p><span className="done-chip"><Icon name="check" size={13} /> Done</span></Agent>
        </div>
      );
    default:
      return (
        <div className="scr scr-voice">
          <div className="wave" aria-hidden="true">{Array.from({ length: 28 }, (_, i) => <i key={i} style={{ animationDelay: `${(i % 7) * -0.12}s` }} />)}</div>
          <p className="voice-said">“Hey Echo, add milk to my shopping list.”</p>
          <p className="voice-reply"><Face size={20} /> Added. That's four things on the list.</p>
        </div>
      );
  }
}

function WaysShowcase({ motionPaused = false }: { motionPaused?: boolean }) {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused || motionPaused || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const t = window.setTimeout(() => setActive((v) => (v + 1) % MODES.length), 6000);
    return () => window.clearTimeout(t);
  }, [active, paused, motionPaused]);
  const mode = MODES[active];
  return (
    <section className="ways shell" id="ways" aria-label="What Echo does">
      <div className="ways-grid" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
        <Reveal className="ways-side" anim="left">
        <div className="ways-tabs" role="tablist" aria-label="Ways Echo helps">
          {MODES.map((m, i) => (
            <button key={m.id} type="button" role="tab" aria-selected={i === active} className={i === active ? "is-on" : ""} onClick={() => setActive(i)}>
              <Icon name={m.icon} size={18} />
              <span>{m.label}</span>
              {i === active && !paused && !motionPaused ? <i className="tab-timer" key={active} /> : null}
            </button>
          ))}
        </div>
        </Reveal>
        <Reveal className="ways-screen" anim="right">
        <div className="ways-screen-inner" role="tabpanel" aria-label={mode.label}>
          <div className="screen-body" key={mode.id}>
            <Screen mode={mode.id} />
          </div>
          <div className="chat-composer" aria-hidden="true"><span>Ask Echo anything...</span><b><Icon name="arrow" size={15} /></b></div>
        </div>
        <p className="ways-caption" key={`c-${mode.id}`}><Face size={18} /><span><b>{mode.says}</b> {mode.caption}</span></p>
        </Reveal>
      </div>
    </section>
  );
}

// ── Real answer cards ────────────────────────────────────────────────

const DEMO_CHART = {
  kind: "line" as const,
  title: "Two stocks, % change this year (example)",
  labels: ["2026-01-02", "2026-02-02", "2026-03-02", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01", "2026-08-03", "2026-09-01", "2026-10-01"],
  series: [
    { name: "Stock A", values: [0, 4, -3, 9, 18, 14, 22, 19, 27, 31] },
    { name: "Stock B", values: [0, -2, 5, 3, 11, 24, 20, 35, 41, 38] },
  ],
  unit: "%",
  source: "Example data",
};
function AnswerCards() {
  const tiles: { key: string; ask: string; className: string; anim: Anim; body: React.ReactNode }[] = [
    { key: "weather", ask: "“What's the weather in Denver this week?”", className: "bento-weather", anim: "left", body: <WeatherCard data={DEMO_WEATHER} /> },
    { key: "chart", ask: "“Compare these two stocks this year.”", className: "bento-chart", anim: "right", body: <ChartView data={DEMO_CHART} /> },
  ];
  return (
    <section className="answers shell" aria-labelledby="answers-title">
      <Reveal className="section-head" anim="blur">
        <span className="kicker">Answers you can see</span>
        <h2 id="answers-title">Less reading.<br /><span className="dim">More seeing.</span></h2>
      </Reveal>
      <div className="bento">
        {tiles.map((t) => (
          <Reveal key={t.key} anim={t.anim} className={`bento-tile ${t.className}`}>
            <p className="bento-ask"><span className="bento-you">You</span>{t.ask}</p>
            <div className="bento-card">{t.body}</div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

// ── The team ─────────────────────────────────────────────────────────

const TEAM = [
  { name: "Echo", tone: "light" as const, role: "Your personal agent", line: "Talks it through, plans, remembers what matters to you.", quote: "Leave it with me." },
  { name: "Jarvis", tone: "dark" as const, role: "The researcher", line: "Searches the web, reads the pages, brings back sources.", quote: "Found three good sources." },
  { name: "Glados", tone: "dark" as const, role: "The builder", line: "Writes code, runs it, fixes it, saves the files.", quote: "Tests pass. Shipping it." },
];

function Team() {
  return (
    <section className="team shell" id="team" aria-labelledby="team-title">
      <Reveal className="section-head" anim="blur">
        <span className="kicker">The team</span>
        <h2 id="team-title">Three agents.<br /><span className="dim">One group chat.</span></h2>
      </Reveal>
      <div className="team-grid">
        {TEAM.map((m, i) => (
          <Reveal key={m.name} className="member" anim="zoom" delay={i * 90}>
            <div className="member-face" style={{ animationDelay: `${i * -1.4}s` }}>
              <Face tone={m.tone} size={92} />
            </div>
            <p className="member-quote">“{m.quote}”</p>
            <h3>{m.name}</h3>
            <span className="member-role">{m.role}</span>
            <p>{m.line}</p>
          </Reveal>
        ))}
        <Reveal className="member member-new" anim="zoom" delay={270}>
          <div className="member-face member-plus" aria-hidden="true">+</div>
          <h3>Your own</h3>
          <span className="member-role">Make an agent</span>
          <p>Give it a name, a personality and the tools it's allowed to use.</p>
        </Reveal>
      </div>
    </section>
  );
}

// ── Big ad panels ────────────────────────────────────────────────────

function ModelsPanel() {
  return (
    <div className="panels shell">
      <Reveal as="section" className="panel panel-dark panel-models" anim="zoom">
        <div className="panel-copy">
          <span className="kicker">Bring your own brain</span>
          <h2>Free local models. <span className="dim">Or your favourite cloud one.</span></h2>
          <p>Run a model on your own GPU for free, or connect OpenAI, Gemini, Claude, or Grok with your own API key. Switch any time.</p>
        </div>
        <div className="marquee" aria-label="Works with LM Studio, Ollama, llama.cpp, vLLM, LocalAI, OpenAI, Gemini, Claude and Grok">
          <div className="marquee-track">
            {[0, 1].map((k) => (
              <div className="marquee-set" key={k} aria-hidden={k === 1}>
                {["LM Studio", "Ollama", "llama.cpp", "vLLM", "LocalAI", "OpenAI", "Gemini", "Claude", "Grok", "Gemma", "Qwen", "Llama"].map((n) => <span key={n}>{n}</span>)}
              </div>
            ))}
          </div>
        </div>
      </Reveal>
    </div>
  );
}

// ── About ────────────────────────────────────────────────────────────

const PILLARS: { icon: IconName; title: string; copy: string }[] = [
  { icon: "github", title: "Open source", copy: "Every line is public on GitHub. Read it, change it, make it yours." },
  { icon: "house", title: "A home for your data", copy: "Chats, memory and files are stored on your PC. Cloud models receive the requests you choose to send." },
  { icon: "spark", title: "A decentralized agent", copy: "No account, no middleman. Echo runs where you are, on the model you choose." },
  { icon: "check", title: "Gets things done", copy: "Echo doesn't just answer. He searches, writes, builds and finishes the job." },
  { icon: "team", title: "Talks to other agents", copy: "He works with Jarvis, Glados and agents you make, and can connect to agents elsewhere (A2A)." },
  { icon: "memory", title: "Becomes yours", copy: "He remembers what matters to you and grows into your own personal agent." },
];

function About() {
  return (
    <section className="about shell" id="about" aria-labelledby="about-title">
      <Reveal className="section-head" anim="blur">
        <span className="kicker">About Echo</span>
        <h2 id="about-title">Your own agent.<br /><span className="dim">Not someone else's.</span></h2>
      </Reveal>
      <div className="pillars">
        {PILLARS.map((pillar, i) => (
          <Reveal key={pillar.title} className="pillar" anim="up" delay={(i % 3) * 90}>
            <span className="pillar-icon" style={{ animationDelay: `${i * -0.8}s` }}><Icon name={pillar.icon} size={20} /></span>
            <h3>{pillar.title}</h3>
            <p>{pillar.copy}</p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

// ── Download ─────────────────────────────────────────────────────────

function BringHome() {
  const release = useLatestRelease();
  return (
    <Reveal as="section" className="bring shell" id="download" anim="zoom">
      <div className="bring-mark" aria-hidden="true">
        <span className="bring-face"><i /><i /></span>
      </div>
      <div className="bring-copy">
        <span className="kicker">Download</span>
        <h2>Bring Echo home.</h2>
        <p>Install, pick a model, say hi. It updates itself from then on.</p>
        <div className="bring-actions">
          <DownloadButton variant="light" />
          <Link className="text-link" to="/docs/getting-started">Setup guide <Icon name="arrow" size={15} /></Link>
          <a className="text-link" href={release.notesUrl} target="_blank" rel="noreferrer">What's new <Icon name="arrow" size={15} /></a>
        </div>
        <small className="bring-note">Windows 10 / 11 · 64-bit · free and open source (MIT) · <a href={GITHUB_URL} target="_blank" rel="noreferrer">source on GitHub</a></small>
      </div>
    </Reveal>
  );
}

export function Home() {
  const [motionPaused, setMotionPaused] = useState(false);
  return (
    <div className="site site-home" data-motion={motionPaused ? "paused" : "running"}>
      <MotionControls paused={motionPaused} onToggle={() => setMotionPaused(value => !value)} />
      <style>{echoFaceStyles}</style>
      <SiteHeader />
      <main>
        <div className="snap snap-hero"><Hero /></div>
        <div className="snap snap-up"><About /></div>
        <div className="snap"><WaysShowcase motionPaused={motionPaused} /></div>
        <div className="snap"><CreationsStory /></div>
        <div className="snap"><ResearchStory /></div>
        <div className="snap"><SetupStory /></div>
        <div className="snap snap-up"><AnswerCards /></div>
        <div className="snap snap-up"><Team /></div>
        <div className="snap snap-last"><div className="last-pair"><ModelsPanel /><BringHome /></div><SiteFooter /></div>
      </main>
    </div>
  );
}
