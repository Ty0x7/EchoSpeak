import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EchoFace, echoFaceStyles } from "../components/EchoFace";
import { ChartView } from "../widgets/Chart";
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
  return (
    <section className="hero shell">
      <div className="hero-copy">
        <h1>Meet <span className="hl">Echo</span>, the AI that lives on your computer.</h1>
        <p className="hero-lede">Ask anything, get answers you can see, and let Echo and his team actually do the work. Your data stays with you.</p>
        <div className="hero-actions">
          <DownloadButton />
          <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer"><Icon name="github" size={18} /> View on GitHub</a>
        </div>
        <ul className="hero-values">
          <li><Icon name="github" size={15} /> Open source</li>
          <li><Icon name="house" size={15} /> Your data stays on your PC</li>
          <li><Icon name="model" size={15} /> Local or cloud models</li>
        </ul>
      </div>
      <div className="hero-face"><EchoFace size="clamp(180px, 24vw, 300px)" /></div>
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
          <p className="msg msg-you">Plan a cozy Saturday for two. Not too expensive.</p>
          <div className="msg msg-echo">
            <Face size={22} />
            <div>
              <p>Here's a relaxed day under $60:</p>
              <ul>
                <li>Morning coffee and a long walk somewhere green</li>
                <li>A free museum or gallery in the afternoon</li>
                <li>Dinner from a local market, picnic style</li>
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
          <p className="voice-said">“Hey Echo, add milk to my shopping list.”</p>
          <p className="voice-reply"><Face size={20} /> Added. That's four things on the list.</p>
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
    <section className="ways shell" id="ways" aria-label="What Echo does">
      <div className="ways-grid" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
        <Reveal className="ways-side" anim="left">
        <div className="ways-tabs" role="tablist" aria-label="Ways Echo helps">
          {MODES.map((m, i) => (
            <button key={m.id} type="button" role="tab" aria-selected={i === active} className={i === active ? "is-on" : ""} onClick={() => setActive(i)}>
              <Icon name={m.icon} size={18} />
              <span>{m.label}</span>
              {i === active && !paused ? <i className="tab-timer" key={active} /> : null}
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

function HomePanel() {
  return (
    <div className="panels shell">
      <Reveal as="section" className="panel panel-light panel-home" anim="flip">
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
          <div className="house-face"><Face tone="dark" size={92} /></div>
        </div>
      </Reveal>
    </div>
  );
}

/** Smoothly counts a number toward its new value. */
function useTween(value: number, ms = 450) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = performance.now();
    const begin = from.current;
    let raf = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      const v = begin + (value - begin) * (1 - Math.pow(1 - t, 3));
      from.current = v;
      setShown(v);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);
  return shown;
}

const SPLIT_NAMES = ["You", "Sam", "Alex", "Jo", "Kim", "Lee"];

function SplitApp({ version }: { version: 1 | 2 }) {
  const [bill, setBill] = useState(84);
  const [tip, setTip] = useState(18);
  const [people, setPeople] = useState(3);
  const total = bill * (1 + tip / 100);
  const split = version === 2;
  const each = split ? total / people : total;
  const shownMain = useTween(each);
  const shownTotal = useTween(total);
  return (
    <div className="splitapp">
      <div className="split-hero">
        <span>{split ? "Each person pays" : "Total with tip"}</span>
        <strong>${shownMain.toFixed(2)}</strong>
        <small>{split ? `Total $${shownTotal.toFixed(2)} · ${people} people` : `Tip $${(total - bill).toFixed(2)}`}</small>
      </div>
      <label className="split-row">
        <span>Bill</span>
        <b>${bill}</b>
        <input type="range" min={10} max={300} value={bill} onChange={(e) => setBill(Number(e.target.value))} aria-label="Bill amount" />
      </label>
      <div className="split-row">
        <span>Tip</span>
        <div className="split-seg" role="radiogroup" aria-label="Tip">
          {[10, 15, 18, 20, 25].map((t) => (
            <button key={t} type="button" role="radio" aria-checked={tip === t} className={tip === t ? "is-on" : ""} onClick={() => setTip(t)}>{t}%</button>
          ))}
        </div>
      </div>
      {split ? (
        <div className="split-row split-people">
          <span>People</span>
          <div className="split-stepper">
            <button type="button" onClick={() => setPeople((n) => Math.max(1, n - 1))} aria-label="Fewer people">−</button>
            <b>{people}</b>
            <button type="button" onClick={() => setPeople((n) => Math.min(6, n + 1))} aria-label="More people">+</button>
          </div>
          <div className="split-faces">
            {SPLIT_NAMES.slice(0, people).map((n) => (
              <span key={n} className="split-face"><i>{n[0]}</i><small>${each.toFixed(2)}</small></span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SplitCode({ version }: { version: 1 | 2 }) {
  return (
    <pre className="split-code" aria-label="App code">
      <code>
        <span className="c-k">const</span> total = bill * (<span className="c-n">1</span> + tip / <span className="c-n">100</span>);{"\n"}
        {version === 2 ? <><span className="c-k">const</span> each = total / people;{"\n"}</> : null}
        {"\n"}
        <span className="c-k">return</span> ({"\n"}
        {"  "}&lt;<span className="c-t">Card</span>&gt;{"\n"}
        {"    "}&lt;<span className="c-t">Big</span>&gt;{"{"}money({version === 2 ? "each" : "total"}){"}"}&lt;/<span className="c-t">Big</span>&gt;{"\n"}
        {"    "}&lt;<span className="c-t">Slider</span> label=<span className="c-s">"Bill"</span> /&gt;{"\n"}
        {"    "}&lt;<span className="c-t">Pills</span> options={"{"}[<span className="c-n">10</span>, <span className="c-n">15</span>, <span className="c-n">18</span>, <span className="c-n">20</span>]{"}"} /&gt;{"\n"}
        {version === 2 ? <>{"    "}&lt;<span className="c-t">People</span> max={"{"}<span className="c-n">6</span>{"}"} /&gt;{"\n"}</> : null}
        {"  "}&lt;/<span className="c-t">Card</span>&gt;{"\n"}
        );
      </code>
    </pre>
  );
}

function BuildDemo() {
  const [version, setVersion] = useState<1 | 2>(2);
  const [view, setView] = useState<"preview" | "code">("preview");
  return (
    <div className="build-demo">
      <div className="build-chat">
        <p className="msg msg-you">Build me a tip calculator.</p>
        <div className="msg msg-echo">
          <Face size={22} />
          <div>
            <p>Here you go. It's open on the right.</p>
            <button type="button" className={`build-chip${version === 1 ? " is-on" : ""}`} onClick={() => setVersion(1)}><Icon name="code" size={14} /><span><strong>Tip calculator</strong><small>App · v1</small></span></button>
          </div>
        </div>
        <p className="msg msg-you">Make it split the bill between friends.</p>
        <div className="msg msg-echo">
          <Face size={22} />
          <div>
            <p>Done. Version 2 splits it and shows what each person pays.</p>
            <button type="button" className={`build-chip${version === 2 ? " is-on" : ""}`} onClick={() => setVersion(2)}><Icon name="code" size={14} /><span><strong>Tip & split</strong><small>App · v2</small></span></button>
          </div>
        </div>
        <div className="chat-composer" aria-hidden="true"><span>Ask for a change...</span><b><Icon name="arrow" size={15} /></b></div>
      </div>
      <div className="build-artifact">
        <div className="build-bar">
          <strong>{version === 2 ? "Tip & split" : "Tip calculator"}</strong>
          <div className="build-versions" role="group" aria-label="Version">
            {[1, 2].map((v) => (
              <button key={v} type="button" className={version === v ? "is-on" : ""} onClick={() => setVersion(v as 1 | 2)}>v{v}</button>
            ))}
          </div>
          <div className="build-tabs" role="tablist" aria-label="View">
            <button type="button" role="tab" aria-selected={view === "preview"} className={view === "preview" ? "is-on" : ""} onClick={() => setView("preview")}>Preview</button>
            <button type="button" role="tab" aria-selected={view === "code"} className={view === "code" ? "is-on" : ""} onClick={() => setView("code")}>Code</button>
          </div>
        </div>
        <div className="build-stage" key={`${version}-${view}`}>
          {view === "preview" ? <SplitApp version={version} /> : <SplitCode version={version} />}
        </div>
      </div>
    </div>
  );
}

function BuildPanel() {
  return (
    <div className="panels shell">
      <Reveal as="section" className="panel panel-dark panel-build" anim="zoom">
        <div className="panel-copy">
          <span className="kicker">Artifacts</span>
          <h2>Ask for an app. <span className="dim">Get an app.</span></h2>
        </div>
        <BuildDemo />
        <p className="build-hint">It's live. Drag the bill, add people, flip between versions.</p>
      </Reveal>
    </div>
  );
}

function SafePanels() {
  return (
    <div className="panels shell">
      <Reveal as="section" className="panel panel-light panel-safe" anim="left">
        <div className="panel-copy">
          <span className="kicker">You're in charge</span>
          <h2>Asks before<br />anything risky.</h2>
          <p>Reading and searching just happen. Deleting, sending or anything after reading a sketchy web page waits for your OK.</p>
        </div>
        <ApprovalDemo />
      </Reveal>

      <Reveal as="section" className="panel panel-dark panel-models" anim="right" delay={120}>
        <div className="panel-copy">
          <span className="kicker">Bring your own brain</span>
          <h2>Free local models.<br /><span className="dim">Or your favourite cloud one.</span></h2>
          <p>Run a model on your own GPU for free, or plug in an OpenAI or Gemini key. Switch any time.</p>
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

/**
 * While the hero is on screen, the first downward scroll (wheel, swipe or key) glides
 * straight to About instead of creeping through the gap. After that, scrolling is free.
 */
function useHeroSnap(targetId: string) {
  useEffect(() => {
    let animating = false;
    let touchY: number | null = null;
    const headerOffset = 68;

    const target = () => {
      const el = document.getElementById(targetId);
      return el ? el.getBoundingClientRect().top + window.scrollY - headerOffset : null;
    };
    // Only snap while we're still up in the hero.
    const inHero = () => {
      const top = target();
      return top !== null && window.scrollY < top - 40;
    };
    const glide = () => {
      const to = target();
      if (to === null) return;
      const from = window.scrollY;
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (reduce) {
        window.scrollTo({ top: to, behavior: "auto" });
        return;
      }
      animating = true;
      const html = document.documentElement;
      const prevBehavior = html.style.scrollBehavior;
      html.style.scrollBehavior = "auto";
      const duration = 900;
      const start = performance.now();
      const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
      const step = (now: number) => {
        const t = Math.min(1, (now - start) / duration);
        window.scrollTo(0, from + (to - from) * ease(t));
        if (t < 1) {
          requestAnimationFrame(step);
        } else {
          html.style.scrollBehavior = prevBehavior;
          // Swallow the tail of a trackpad fling so it doesn't overshoot.
          window.setTimeout(() => { animating = false; }, 250);
        }
      };
      requestAnimationFrame(step);
    };

    const onWheel = (e: WheelEvent) => {
      if (animating) {
        e.preventDefault();
        return;
      }
      if (e.deltaY > 0 && inHero()) {
        e.preventDefault();
        glide();
      }
    };
    const onTouchStart = (e: TouchEvent) => {
      touchY = e.touches[0]?.clientY ?? null;
    };
    const onTouchMove = (e: TouchEvent) => {
      if (animating) {
        e.preventDefault();
        return;
      }
      const y = e.touches[0]?.clientY;
      if (touchY === null || y === undefined) return;
      if (touchY - y > 12 && inHero()) {
        e.preventDefault();
        touchY = null;
        glide();
      }
    };
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(el.tagName))) return;
      if (["ArrowDown", "PageDown", " "].includes(e.key) && !e.shiftKey && inHero()) {
        e.preventDefault();
        if (!animating) glide();
      }
    };

    window.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("touchstart", onTouchStart, { passive: true });
    window.addEventListener("touchmove", onTouchMove, { passive: false });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("touchstart", onTouchStart);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("keydown", onKey);
    };
  }, [targetId]);
}

export function Home() {
  useHeroSnap("about");
  return (
    <div className="site">
      <style>{echoFaceStyles}</style>
      <SiteHeader />
      <main>
        <Hero />
        <About />
        <WaysShowcase />
        <HomePanel />
        <AnswerCards />
        <BuildPanel />
        <Team />
        <SafePanels />
        <BringHome />
      </main>
      <SiteFooter />
    </div>
  );
}
