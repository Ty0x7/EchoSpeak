import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EchoFace, echoFaceStyles } from "../components/EchoFace";
import { ChartView } from "../widgets/Chart";
import { ScoreCard } from "../widgets/Cards";
import { Timeline } from "../widgets/Blocks";
import { WeatherCard } from "../widgets/Weather";
import "../widgets/widgets.css";
import { DownloadButton, Face, Icon, SiteFooter, SiteHeader, type IconName } from "./Chrome";
import { GITHUB_URL, useLatestRelease } from "./release";
import "./site.css";

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

function Reveal({ children, className = "", as: Tag = "div", id }: { children: React.ReactNode; className?: string; as?: "div" | "section"; id?: string }) {
  const { ref, visible } = useReveal<HTMLDivElement>();
  return (
    <Tag ref={ref as any} id={id} className={`reveal ${className}`} data-visible={visible ? "true" : "false"}>
      {children}
    </Tag>
  );
}

// ── Hero ─────────────────────────────────────────────────────────────

const ORBIT_ICONS: IconName[] = ["chat", "research", "code", "chart", "team", "voice", "memory", "shield"];

function HeroArt() {
  return (
    <div className="hero-art" aria-hidden="true">
      <div className="hero-glow" />
      <div className="orbit o1"><span className="orbit-dot" /></div>
      <div className="orbit o2"><span className="orbit-dot" /></div>
      <div className="orbit o3" />
      <div className="orbit-icons">
        {ORBIT_ICONS.map((icon, i) => (
          <span key={icon} className="orbit-icon" style={{ ["--a" as string]: `${(360 / ORBIT_ICONS.length) * i}deg` }}>
            <span><Icon name={icon} size={18} /></span>
          </span>
        ))}
      </div>
      <div className="hero-face"><EchoFace size="clamp(150px, 22vw, 250px)" /></div>
    </div>
  );
}

function Hero() {
  return (
    <section className="hero">
      <div className="hero-dots" aria-hidden="true" />
      <div className="hero-inner shell">
        <div className="hero-copy">
          <p className="pill-row"><span className="pill">Free</span><span className="pill">Open source</span><span className="pill">Runs on your PC</span></p>
          <h1>Meet <span className="hl">Echo</span>.<br />The AI that lives<br />on your computer.</h1>
          <p className="hero-lede">Ask anything, get real answers you can see, and let Echo and his team actually do the work.</p>
          <div className="hero-actions">
            <DownloadButton />
            <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer"><Icon name="github" size={18} /> View on GitHub</a>
          </div>
        </div>
        <HeroArt />
      </div>
    </section>
  );
}

// ── One Echo, many ways ─────────────────────────────────────────────

type Mode = { id: string; icon: IconName; label: string; says: string; caption: string };
const MODES: Mode[] = [
  { id: "chat", icon: "chat", label: "Talk it through", says: "Ask me anything.", caption: "Plans, advice, explanations: talk to Echo like a person." },
  { id: "see", icon: "chart", label: "Answers you can see", says: "Here's the week.", caption: "Weather, stocks, scores and more show up as cards, not walls of text." },
  { id: "research", icon: "research", label: "Look things up", says: "I read the sources.", caption: "Echo searches the web, reads the pages and shows where answers came from." },
  { id: "build", icon: "code", label: "Build things", says: "Made you an app.", caption: "Ask for a calculator, a game or a document and use it right next to the chat." },
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

function Screen({ mode }: { mode: string }) {
  switch (mode) {
    case "chat":
      return (
        <div className="scr scr-chat">
          <p className="msg msg-you">Plan a cozy Saturday for two in Edmonton. Not too expensive.</p>
          <div className="msg msg-echo">
            <Face size={22} />
            <div>
              <p>Here's a relaxed day under $60:</p>
              <ul>
                <li>Morning coffee and a walk by the river valley</li>
                <li>Afternoon at the Royal Alberta Museum</li>
                <li>Dinner from the Old Strathcona market, picnic style</li>
              </ul>
              <p className="muted">Want me to check the weather first?</p>
            </div>
          </div>
        </div>
      );
    case "see":
      return (
        <div className="scr scr-see">
          <p className="msg msg-you">What's the weather in Denver this week?</p>
          <WeatherCard data={DEMO_WEATHER} />
        </div>
      );
    case "research":
      return (
        <div className="scr scr-research">
          <p className="msg msg-you">Is an air fryer actually healthier than an oven?</p>
          <div className="msg msg-echo">
            <Face size={22} />
            <div>
              <p>Mostly the same food, less oil: air fryers cook with hot moving air, so you get crispy results with a spoonful of oil instead of a pan of it.</p>
              <div className="chips">
                {["healthline.com", "mayoclinic.org", "seriouseats.com"].map((s, i) => <span key={s} className="chip"><b>{i + 1}</b>{s}</span>)}
              </div>
            </div>
          </div>
        </div>
      );
    case "build":
      return (
        <div className="scr scr-build">
          <p className="msg msg-you">Build me a tip calculator.</p>
          <div className="artifact-chip"><Icon name="code" size={16} /><span><strong>Tip calculator</strong><small>App · v1</small></span><em>Open</em></div>
          <MiniTipCalc compact />
        </div>
      );
    case "team":
      return (
        <div className="scr scr-team">
          <p className="msg msg-you">@Echo get Jarvis to find a good pasta recipe and have Glados save it to recipes.txt</p>
          <div className="team-line"><Face size={20} /><span><b>Echo</b> Asking Jarvis to find a recipe…</span></div>
          <div className="team-line"><Face tone="dark" size={20} /><span><b>Jarvis</b> Found a 20-minute garlic pasta (3 sources).</span></div>
          <div className="team-line"><Face tone="dark" size={20} /><span><b>Glados</b> Saved to recipes.txt.</span></div>
          <div className="done-line"><Icon name="check" size={14} /> Done: recipe found and saved.</div>
        </div>
      );
    default:
      return (
        <div className="scr scr-voice">
          <div className="wave" aria-hidden="true">{Array.from({ length: 28 }, (_, i) => <i key={i} style={{ animationDelay: `${(i % 7) * -0.12}s` }} />)}</div>
          <p className="voice-said">“Hey Echo, remind me what my sister's name is.”</p>
          <p className="voice-reply"><Face size={20} /> It's Emily. Want me to text her?</p>
        </div>
      );
  }
}

function WaysShowcase() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const t = window.setTimeout(() => setActive((v) => (v + 1) % MODES.length), 6000);
    return () => window.clearTimeout(t);
  }, [active, paused]);
  const mode = MODES[active];
  return (
    <section className="ways shell" id="ways" aria-labelledby="ways-title">
      <Reveal className="section-head">
        <span className="kicker">One Echo · many ways to help</span>
        <h2 id="ways-title">He changes how he helps.<br /><span className="dim">Never who he is.</span></h2>
      </Reveal>
      <div className="ways-grid" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
        <div className="ways-tabs" role="tablist" aria-label="Ways Echo helps">
          {MODES.map((m, i) => (
            <button key={m.id} type="button" role="tab" aria-selected={i === active} className={i === active ? "is-on" : ""} onClick={() => setActive(i)}>
              <Icon name={m.icon} size={18} />
              <span>{m.label}</span>
              {i === active && !paused ? <i className="tab-timer" key={active} /> : null}
            </button>
          ))}
        </div>
        <div className="ways-screen" role="tabpanel" aria-label={mode.label}>
          <div className="screen-body" key={mode.id}>
            <Screen mode={mode.id} />
          </div>
          <p className="ways-caption" key={`c-${mode.id}`}><Face size={20} /><span><b>{mode.says}</b> {mode.caption}</span></p>
        </div>
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
const DEMO_SCORES = {
  title: "Edmonton Oilers (example)",
  games: [
    { home: "Edmonton Oilers", away: "Calgary Flames", home_score: 4, away_score: 2, status: "Final", league: "NHL", state: "post", home_record: "5-1", away_record: "3-3", home_abbr: "EDM", away_abbr: "CGY" },
    { home: "Vancouver Canucks", away: "Edmonton Oilers", home_score: null, away_score: null, status: "Sat 7:00 PM", league: "NHL", state: "pre", home_abbr: "VAN", away_abbr: "EDM" },
  ],
};
const DEMO_STEPS = {
  title: "Set up a local AI model",
  ordered: true,
  items: [
    { title: "Install LM Studio", detail: "Free app that runs AI models on your PC." },
    { title: "Download a model", detail: "Gemma 4 E4B is a great start on an 8 GB GPU." },
    { title: "Pick it in EchoSpeak", detail: "Settings › Models › LM Studio. Done." },
  ],
};

function AnswerCards() {
  const tiles: { key: string; ask: string; className: string; body: React.ReactNode }[] = [
    { key: "weather", ask: "“What's the weather in Denver this week?”", className: "bento-weather", body: <WeatherCard data={DEMO_WEATHER} /> },
    { key: "chart", ask: "“Compare these two stocks this year.”", className: "bento-chart", body: <ChartView data={DEMO_CHART} /> },
    { key: "scores", ask: "“How are the Oilers doing?”", className: "bento-scores", body: <ScoreCard data={DEMO_SCORES} /> },
    { key: "steps", ask: "“How do I set up a local model?”", className: "bento-steps", body: <Timeline data={DEMO_STEPS} /> },
  ];
  return (
    <section className="answers shell" aria-labelledby="answers-title">
      <Reveal className="section-head">
        <span className="kicker">Answers you can see</span>
        <h2 id="answers-title">Less reading.<br /><span className="dim">More seeing.</span></h2>
        <p className="section-lede">Weather, charts, scores, products, videos and how-tos show up as cards, built from real data.</p>
      </Reveal>
      <div className="bento">
        {tiles.map((t) => (
          <Reveal key={t.key} className={`bento-tile ${t.className}`}>
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
      <Reveal className="section-head">
        <span className="kicker">The team</span>
        <h2 id="team-title">Three agents.<br /><span className="dim">One group chat.</span></h2>
        <p className="section-lede">@mention who you want, or let them sort it out. They hand work to each other and tell you when it's actually done.</p>
      </Reveal>
      <div className="team-grid">
        {TEAM.map((m, i) => (
          <Reveal key={m.name} className="member">
            <div className="member-face" style={{ animationDelay: `${i * -1.4}s` }}>
              <Face tone={m.tone} size={92} />
            </div>
            <p className="member-quote">“{m.quote}”</p>
            <h3>{m.name}</h3>
            <span className="member-role">{m.role}</span>
            <p>{m.line}</p>
          </Reveal>
        ))}
        <Reveal className="member member-new">
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

function MiniTipCalc({ compact = false }: { compact?: boolean }) {
  const [bill, setBill] = useState(64);
  const [tip, setTip] = useState(18);
  const [people, setPeople] = useState(2);
  const total = bill * (1 + tip / 100);
  return (
    <div className={`tipcalc${compact ? " is-compact" : ""}`}>
      <label>Bill <span>${bill}</span><input type="range" min={10} max={300} value={bill} onChange={(e) => setBill(Number(e.target.value))} aria-label="Bill amount" /></label>
      <div className="tip-pills" role="radiogroup" aria-label="Tip">
        {[15, 18, 20, 25].map((t) => (
          <button key={t} type="button" role="radio" aria-checked={tip === t} className={tip === t ? "is-on" : ""} onClick={() => setTip(t)}>{t}%</button>
        ))}
      </div>
      {!compact ? (
        <label>People <span>{people}</span><input type="range" min={1} max={8} value={people} onChange={(e) => setPeople(Number(e.target.value))} aria-label="People" /></label>
      ) : null}
      <div className="tip-total"><span>{compact ? "Total" : "Each pays"}</span><strong>${(compact ? total : total / people).toFixed(2)}</strong></div>
    </div>
  );
}

function ApprovalDemo() {
  const [state, setState] = useState<"ask" | "allow" | "deny">("ask");
  return (
    <div className="approval-demo">
      <div className="approval-head"><Face size={22} /><span><b>Echo</b> wants to</span></div>
      <p className="approval-what">Delete <code>old-notes.txt</code> from your project</p>
      {state === "ask" ? (
        <div className="approval-actions">
          <button type="button" className="ok" onClick={() => setState("allow")}>Allow</button>
          <button type="button" onClick={() => setState("deny")}>Deny</button>
        </div>
      ) : (
        <p className="approval-result">
          {state === "allow" ? <><Icon name="check" size={15} /> Deleted. It's in your undo history if you change your mind.</> : <>No problem, I'll leave it alone.</>}
          <button type="button" className="again" onClick={() => setState("ask")}>Try again</button>
        </p>
      )}
    </div>
  );
}

function Panels() {
  return (
    <div className="panels shell">
      <Reveal as="section" className="panel panel-light panel-home">
        <div className="panel-copy">
          <span className="kicker">Private by default</span>
          <h2>Lives on your PC.<br />Not in someone's cloud.</h2>
          <p>Your chats, memory and files stay on your computer. Use a free local model and nothing leaves at all.</p>
        </div>
        <div className="house-art" aria-hidden="true">
          <svg viewBox="0 0 300 240" className="house-svg">
            <path d="M30 120 150 28l120 92" />
            <path d="M58 100v118h184V100" />
          </svg>
          <div className="house-face"><Face tone="dark" size={86} /></div>
          <span className="house-chip c1">Chats</span>
          <span className="house-chip c2">Memory</span>
          <span className="house-chip c3">Files</span>
        </div>
      </Reveal>

      <Reveal as="section" className="panel panel-dark panel-build">
        <div className="panel-copy">
          <span className="kicker">Artifacts</span>
          <h2>Ask for an app.<br /><span className="dim">Get an app.</span></h2>
          <p>Calculators, games, documents, diagrams. They open next to the chat, and “make it also split the bill” gives you version 2. Try this one:</p>
        </div>
        <div className="artifact-frame">
          <div className="artifact-bar"><strong>Tip & split calculator</strong><small>App · v2 of 2</small></div>
          <MiniTipCalc />
        </div>
      </Reveal>

      <Reveal as="section" className="panel panel-light panel-safe">
        <div className="panel-copy">
          <span className="kicker">You're in charge</span>
          <h2>Asks before<br />anything risky.</h2>
          <p>Reading and searching just happen. Deleting, sending or anything after reading a sketchy web page waits for your OK.</p>
        </div>
        <ApprovalDemo />
      </Reveal>

      <Reveal as="section" className="panel panel-dark panel-models">
        <div className="panel-copy">
          <span className="kicker">Bring your own brain</span>
          <h2>Free local models.<br /><span className="dim">Or your favourite cloud one.</span></h2>
        </div>
        <div className="marquee" aria-label="Works with LM Studio, Ollama, llama.cpp, vLLM, LocalAI, OpenAI and Gemini">
          <div className="marquee-track">
            {[0, 1].map((k) => (
              <div className="marquee-set" key={k} aria-hidden={k === 1}>
                {["LM Studio", "Ollama", "llama.cpp", "vLLM", "LocalAI", "OpenAI", "Gemini", "Gemma", "Qwen", "Llama"].map((n) => <span key={n}>{n}</span>)}
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
  { icon: "house", title: "Your data goes to you", copy: "Chats, memory and files live on your PC, not on a company's servers." },
  { icon: "spark", title: "A decentralized agent", copy: "No account, no middleman. Echo runs where you are, on the model you choose." },
  { icon: "check", title: "Gets things done", copy: "Echo doesn't just answer. He searches, writes, builds and finishes the job." },
  { icon: "team", title: "Talks to other agents", copy: "He works with Jarvis, Glados and agents you make, and can connect to agents elsewhere (A2A)." },
  { icon: "memory", title: "Becomes yours", copy: "He remembers what matters to you and grows into your own personal agent." },
];

function About() {
  return (
    <section className="about shell" id="about" aria-labelledby="about-title">
      <Reveal className="section-head">
        <span className="kicker">About Echo</span>
        <h2 id="about-title">Your own agent.<br /><span className="dim">Not someone else's.</span></h2>
        <p className="section-lede">Echo is an open-source, local-first agent: a personal AI that lives with you, works for you, and teams up with other agents to get real tasks done.</p>
      </Reveal>
      <div className="pillars">
        {PILLARS.map((pillar, i) => (
          <Reveal key={pillar.title} className="pillar">
            <span className="pillar-icon" style={{ animationDelay: `${i * -0.8}s` }}><Icon name={pillar.icon} size={20} /></span>
            <h3>{pillar.title}</h3>
            <p>{pillar.copy}</p>
          </Reveal>
        ))}
      </div>
      <Reveal className="about-card">
        <div className="about-avatar" aria-hidden="true">T</div>
        <div className="about-copy">
          <span className="kicker">The maker</span>
          <h3>Built by one person who wanted a better assistant.</h3>
          <p>Hi, I'm Ty. I build EchoSpeak on my own because I wanted an AI that lives on my own PC, remembers what matters, and actually gets things done instead of just talking about it. It's free, open source, and it gets better every week.</p>
          <div className="about-links">
            <a className="text-link" href={GITHUB_URL} target="_blank" rel="noreferrer"><Icon name="github" size={16} /> Follow along on GitHub</a>
            <a className="text-link" href={`${GITHUB_URL}/issues`} target="_blank" rel="noreferrer">Suggest an idea <Icon name="arrow" size={15} /></a>
          </div>
        </div>
      </Reveal>
    </section>
  );
}

// ── Download ─────────────────────────────────────────────────────────

function BringHome() {
  const release = useLatestRelease();
  return (
    <Reveal as="section" className="bring shell" id="download">
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
  return (
    <div className="site">
      <style>{echoFaceStyles}</style>
      <SiteHeader />
      <main>
        <Hero />
        <WaysShowcase />
        <AnswerCards />
        <Team />
        <Panels />
        <About />
        <BringHome />
      </main>
      <SiteFooter />
    </div>
  );
}
