import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EchoFace, echoFaceStyles, type EchoFaceMode } from "../components/EchoFace";
import { DownloadButton, Face, Icon, SiteFooter, SiteHeader, type IconName } from "./Chrome";
import { GITHUB_URL, useLatestRelease } from "./release";
import "./site.css";
import "./home.css";

/**
 * The front page, in five beats and in Echo's own voice:
 * 1. Echo says hi (and reacts to you)   2. Watch me work (a looping mini chat)
 * 3. Meet the crew (a group chat)       4. I live on your PC and play it safe
 * 5. Take me home (download).
 * Details live in the docs; this page shows instead of explains.
 */

const reducedMotion = () => typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

/** True once the element is on screen (and, with `live`, false again when it leaves). */
function useInView<T extends HTMLElement>(live = false) {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") { setInView(true); return; }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setInView(true); if (!live) observer.disconnect(); }
      else if (live) setInView(false);
    }, { threshold: 0.25 });
    observer.observe(el);
    return () => observer.disconnect();
  }, [live]);
  return { ref, inView };
}

/** True while a media query matches, following changes such as a window resize. */
function useMedia(query: string) {
  const [match, setMatch] = useState(() => typeof window !== "undefined" && Boolean(window.matchMedia?.(query).matches));
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const update = () => setMatch(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, [query]);
  return match;
}

/**
 * Scroll progress from 0 to 1. "track" is a tall section whose sticky stage stays on screen while it
 * plays; "view" is an element crossing the screen, from its first pixel to its last.
 */
function useScrollProgress<T extends HTMLElement>(mode: "track" | "view" = "track") {
  const ref = useRef<T | null>(null);
  const [p, setP] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let frame = 0;
    const read = () => {
      frame = 0;
      const r = el.getBoundingClientRect();
      const vh = window.innerHeight;
      const range = mode === "track" ? el.offsetHeight - vh : vh + r.height;
      const value = range <= 0 ? 0 : mode === "track" ? -r.top / range : (vh - r.top) / range;
      setP(Math.min(1, Math.max(0, value)));
    };
    const request = () => { if (!frame) frame = window.requestAnimationFrame(read); };
    read();
    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    return () => {
      window.removeEventListener("scroll", request);
      window.removeEventListener("resize", request);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [mode]);
  return { ref, p };
}

/** Slides or rises into place the first time it reaches the screen. */
function Reveal({ from = "up", delay = 0, children }: { from?: "up" | "left" | "right" | "pop"; delay?: number; children: React.ReactNode }) {
  const { ref, inView } = useInView<HTMLDivElement>();
  return (
    <div ref={ref} className={`rv rv-${from}${inView ? " is-in" : ""}`} style={{ "--d": `${delay}ms` } as React.CSSProperties}>
      {children}
    </div>
  );
}

// ── 1. Echo says hi ──────────────────────────────────────────────────

const MOODS: { mode: EchoFaceMode; line: string }[] = [
  { mode: "idle", line: "Hi! I'm Echo." },
  { mode: "listening", line: "I'm all ears. What are we doing?" },
  { mode: "thinking", line: "Hmm… let me look that up." },
  { mode: "working", line: "Building it now. Two secs." },
  { mode: "speaking", line: "Done! I checked it, too." },
];
const POKES = ["Hey! That tickles.", "Boop received.", "Again? Okay, again!", "I'm awake, I promise.", "Careful, I'm ticklish."];
/**
 * Little agents that orbit Echo: the crew plus a few made by "users". Each one rides its own ellipse
 * (rx, ry in px) around him, starting at `start` degrees, so they stay beside him, clear of the text.
 */
const ORBITERS: { s: number; color: string; eyes: string; rx: number; ry: number; speed: number; start: number; dir: 1 | -1 }[] = [
  { s: 30, color: "linear-gradient(180deg, #4f97ff, #2c73e8)", eyes: "#fff", rx: 172, ry: 130, speed: 19, start: 0, dir: 1 },
  { s: 24, color: "#14955a", eyes: "#fff", rx: 160, ry: 118, speed: 23, start: 70, dir: -1 },
  { s: 18, color: "#7c5ce6", eyes: "#fff", rx: 180, ry: 138, speed: 17, start: 140, dir: 1 },
  { s: 22, color: "#f4f4f2", eyes: "#070707", rx: 168, ry: 126, speed: 26, start: 200, dir: -1 },
  { s: 26, color: "linear-gradient(180deg, #4f97ff, #2c73e8)", eyes: "#fff", rx: 176, ry: 136, speed: 21, start: 260, dir: 1 },
  { s: 30, color: "#d6457a", eyes: "#fff", rx: 158, ry: 116, speed: 24, start: 310, dir: -1 },
  { s: 22, color: "#0e9aa7", eyes: "#fff", rx: 182, ry: 140, speed: 18, start: 40, dir: -1 },
  { s: 26, color: "#e07a1f", eyes: "#fff", rx: 166, ry: 124, speed: 22, start: 110, dir: 1 },
  { s: 18, color: "#18181a", eyes: "#fff", rx: 178, ry: 134, speed: 20, start: 180, dir: -1 },
  { s: 16, color: "#f4f4f2", eyes: "#070707", rx: 162, ry: 122, speed: 27, start: 240, dir: 1 },
  { s: 16, color: "#14955a", eyes: "#fff", rx: 170, ry: 128, speed: 16, start: 290, dir: -1 },
];

/** The orbiting agents. Each is moved by a Web Animations keyframe loop; reduced motion leaves them in place. */
function Orbiters() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const still = reducedMotion();
    const animations: Animation[] = [];
    const point = (o: (typeof ORBITERS)[number], deg: number) => {
      const a = (deg * Math.PI) / 180;
      return { x: o.rx * Math.cos(a), y: o.ry * Math.sin(a) };
    };
    root.querySelectorAll<HTMLElement>(".h-orbit").forEach((el, i) => {
      const o = ORBITERS[i];
      if (still || typeof el.animate !== "function") {
        const { x, y } = point(o, o.start);
        el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
        return;
      }
      const steps = 36;
      const frames = Array.from({ length: steps + 1 }, (_, k) => {
        const { x, y } = point(o, o.start + (o.dir * 360 * k) / steps);
        return { transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`, offset: k / steps };
      });
      animations.push(el.animate(frames, { duration: o.speed * 1000, iterations: Infinity, easing: "linear" }));
    });
    return () => animations.forEach((a) => a.cancel());
  }, []);
  return (
    <div className="h-orbits" ref={ref} aria-hidden="true">
      {ORBITERS.map((o, i) => (
        <span key={i} className="h-orbit" style={{ "--s": `${o.s}px` } as React.CSSProperties}>
          <span className="h-mini" style={{ background: o.color, "--eye": o.eyes, "--blink": `${(i % 5) * 1.1}s` } as React.CSSProperties}><i /><i /></span>
        </span>
      ))}
    </div>
  );
}

function Hero() {
  const [mood, setMood] = useState(0);
  const [poke, setPoke] = useState<{ n: number; line: string } | null>(null);
  useEffect(() => {
    if (reducedMotion() || poke) return;
    const timer = window.setTimeout(() => setMood((m) => (m + 1) % MOODS.length), 3200);
    return () => window.clearTimeout(timer);
  }, [mood, poke]);
  useEffect(() => {
    if (!poke) return;
    const timer = window.setTimeout(() => setPoke(null), 1600);
    return () => window.clearTimeout(timer);
  }, [poke]);
  const now = MOODS[mood];
  return (
    <section className="h-hero" id="top" aria-labelledby="hero-title">
      <div className="shell h-hero-grid">
        <div className="h-hero-copy">
          <h1 id="hero-title">Hi, I'm <span className="h-name">Echo<i className="h-type" aria-hidden="true" /></span>.<br />I live on your computer.</h1>
          <p className="h-lede">Ask me anything. I'll look it up, build it, make it, or grab my crew to help. Your files and memories stay with you.</p>
          <div className="h-actions">
            <DownloadButton />
            <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer"><Icon name="github" size={18} /> View on GitHub</a>
          </div>
        </div>
        <div className="h-hero-echo">
          <Orbiters />
          <button type="button" className="h-echo-btn" aria-label="Poke Echo"
            onClick={() => setPoke({ n: Date.now(), line: POKES[Math.floor(Math.random() * POKES.length)] })}>
            <EchoFace size="clamp(150px, 19vw, 230px)" aura mode={poke ? "speaking" : now.mode} />
            {poke ? (
              <span className="h-burst" key={poke.n} aria-hidden="true">
                {Array.from({ length: 14 }, (_, i) => <i key={i} style={{ "--a": `${(360 / 14) * i}deg`, "--d": `${70 + (i % 3) * 26}px` } as React.CSSProperties} />)}
              </span>
            ) : null}
          </button>
          <p className="h-bubble" aria-live="polite" key={poke ? poke.n : mood}>{poke ? poke.line : now.line}</p>
          <small className="h-poke-hint">psst, you can poke me</small>
        </div>
      </div>
    </section>
  );
}

// ── 2. Watch me work ─────────────────────────────────────────────────

type Tool = { icon: IconName; label: string };
type Scenario = { id: string; tab: string; icon: IconName; ask: string; tools: Tool[]; reply: string; extra: "table" | "artifact" | "image"; ctx: number };
const SCENARIOS: Scenario[] = [
  {
    id: "research", tab: "I look it up", icon: "research", ctx: 18,
    ask: "Find a good budget mic for streaming and compare the top three.",
    tools: [{ icon: "research", label: "Searched the web ×3" }, { icon: "file", label: "Read 5 pages" }, { icon: "check", label: "Checked current prices" }],
    reply: "Here are my top three under $100, with the sources I actually read:",
    extra: "table",
  },
  {
    id: "build", tab: "I build it", icon: "code", ctx: 12,
    ask: "Build me a tip calculator I can keep.",
    tools: [{ icon: "spark", label: "Planned the app" }, { icon: "code", label: "Wrote tip-calculator.html" }, { icon: "check", label: "Opened it and tested it" }],
    reply: "Done. It's saved in your Artifacts, so you can open it any time. Try it →",
    extra: "artifact",
  },
  {
    id: "create", tab: "I make it", icon: "image", ctx: 9,
    ask: "Make a poster of a fox astronaut. Cozy, not scary.",
    tools: [{ icon: "spark", label: "Wrote a better prompt" }, { icon: "image", label: "Created the image" }],
    reply: "Here's your fox. I saved it to Creations, too.",
    extra: "image",
  },
];

function timeline(s: Scenario) {
  const typed = 450 + s.ask.length * 28;
  const sent = typed + 250;
  const toolsAt = s.tools.map((_, i) => sent + 550 + i * 850);
  const replyAt = sent + 550 + s.tools.length * 850 + 250;
  const words = s.reply.split(" ").length;
  const extraAt = replyAt + words * 55 + 250;
  return { typed, sent, toolsAt, replyAt, words, extraAt, end: extraAt + 4600 };
}

function TipCalculator() {
  const [bill, setBill] = useState(48);
  const [tip, setTip] = useState(18);
  return (
    <div className="h-tip">
      <label>Bill <span>$<input type="number" min={0} value={bill} onChange={(e) => setBill(Math.max(0, Number(e.target.value) || 0))} aria-label="Bill amount" /></span></label>
      <label>Tip <b>{tip}%</b><input type="range" min={0} max={30} value={tip} onChange={(e) => setTip(Number(e.target.value))} aria-label="Tip percent" /></label>
      <div className="h-tip-total"><span>Total</span><strong>${(bill * (1 + tip / 100)).toFixed(2)}</strong></div>
    </div>
  );
}

function Extra({ kind }: { kind: Scenario["extra"] }) {
  if (kind === "table") {
    return (
      <div className="h-x h-x-table">
        <table>
          <thead><tr><th>Mic</th><th>Price</th><th>Best for</th></tr></thead>
          <tbody>
            <tr><td>Fifine AM8</td><td>$59</td><td>USB now, XLR later</td></tr>
            <tr><td>HyperX SoloCast</td><td>$49</td><td>Plug and play</td></tr>
            <tr><td>Samson Q2U</td><td>$69</td><td>Noisy rooms</td></tr>
          </tbody>
        </table>
        <div className="h-sources">{["rtings.com", "soundguys.com", "youtube.com"].map((s, i) => <span key={s}><b>{i + 1}</b>{s}</span>)}</div>
      </div>
    );
  }
  if (kind === "image") {
    return (
      <div className="h-x h-poster" role="img" aria-label="Example poster: a fox astronaut floating among stars">
        <span className="h-poster-planet" />
        <span className="h-poster-fox"><i /><i /><b /></span>
        <span className="h-poster-title">FOX IN SPACE</span>
      </div>
    );
  }
  return <span className="h-x h-x-saved"><Icon name="file" size={14} /> tip-calculator · App · v1</span>;
}

/** The big line that slides in for each scenario while you scroll the story. */
const BIG: Record<string, string> = { research: "I look it up.", build: "I build it.", create: "I make it." };

function Watch() {
  const still = reducedMotion();
  // On a wide screen the scroll position plays the story: each scenario gets one screen of scrolling.
  // Elsewhere (phones, reduced motion) it plays on its own, as before.
  const scrolly = useMedia("(min-width: 900px) and (prefers-reduced-motion: no-preference)");
  const { ref: trackRef, p } = useScrollProgress<HTMLElement>();
  const { ref, inView } = useInView<HTMLDivElement>(true);
  const [auto, setAuto] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [hold, setHold] = useState(false);
  const count = SCENARIOS.length;
  const scrollAt = p * count;
  const active = scrolly ? Math.min(count - 1, Math.floor(scrollAt)) : auto;
  const scenario = SCENARIOS[active];
  const t = useMemo(() => timeline(scenario), [scenario]);
  const now = scrolly
    ? Math.min(1, Math.max(0, scrollAt - active)) * t.end
    : still ? Number.MAX_SAFE_INTEGER : elapsed;
  const elapsedRef = useRef(0);
  elapsedRef.current = elapsed;
  useEffect(() => {
    if (scrolly || still || !inView || hold) return;
    const started = performance.now() - elapsedRef.current;
    const tick = window.setInterval(() => {
      const e = performance.now() - started;
      if (e >= t.end) { setAuto((a) => (a + 1) % count); setElapsed(0); return; }
      setElapsed(e);
    }, 40);
    return () => window.clearInterval(tick);
  }, [auto, inView, hold, still, scrolly, t, count]);
  const jump = (i: number) => {
    if (!scrolly) { setAuto(i); setElapsed(0); return; }
    const el = trackRef.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    const range = el.offsetHeight - window.innerHeight;
    window.scrollTo({ top: top + ((i + 0.02) / count) * range, behavior: still ? "auto" : "smooth" });
  };
  const typed = scenario.ask.slice(0, Math.max(0, Math.floor((now - 450) / 28)));
  const sent = now >= t.sent;
  const words = scenario.reply.split(" ");
  const shownWords = now >= t.replyAt ? Math.min(words.length, Math.floor((now - t.replyAt) / 55) + 1) : 0;
  const ctx = Math.round(scenario.ctx * Math.min(1, now / t.extraAt));
  const working = sent && now < t.replyAt;
  const showArtifact = scenario.extra === "artifact" && now >= t.extraAt;
  const ring = 2 * Math.PI * 7;
  return (
    <section className={`h-watch-track${scrolly ? " is-scrolly" : ""}`} id="watch" ref={trackRef}
      style={scrolly ? { height: `${(count + 1) * 100}vh` } : undefined} aria-labelledby="watch-title">
      <div className="h-watch-stage" ref={ref}>
        <div className="shell">
          <div className="h-head">
            <span className="kicker">Watch me work</span>
            {scrolly ? <p key={scenario.id} className={`h-big${active % 2 ? " is-right" : ""}`} aria-hidden="true">{BIG[scenario.id]}</p> : null}
            <h2 id="watch-title" className={scrolly ? "h-sr" : undefined}>Ask once. I'll take it from there.</h2>
            {scrolly ? null : <p>I search, read, build and make things, and I show you exactly what I did.</p>}
          </div>
          <div className="h-tabs" role="tablist" aria-label="What I can do">
            {SCENARIOS.map((s, i) => (
              <button key={s.id} type="button" role="tab" aria-selected={i === active} className={i === active ? "is-on" : ""}
                onClick={() => jump(i)}>
                <Icon name={s.icon} size={16} /> {s.tab}
                {i === active && !still ? <i className="h-tab-timer" style={{ width: `${Math.min(100, (now / t.end) * 100)}%` }} /> : null}
              </button>
            ))}
          </div>
          <div className={`h-app${showArtifact ? " has-panel" : ""}`} onMouseEnter={() => setHold(true)} onMouseLeave={() => setHold(false)} aria-label="A sample EchoSpeak chat">
            <div className="h-app-bar"><Face size={16} /><b>EchoSpeak</b><span>{hold ? "paused while you look" : "live demo"}</span></div>
            <div className="h-app-body">
              <aside className="h-app-side" aria-hidden="true">
                {([["spark", "New chat"], ["team", "Group chats"], ["file", "Projects"], ["code", "Artifacts"]] as [IconName, string][]).map(([icon, label]) => (
                  <span key={label}><Icon name={icon} size={13} />{label}</span>
                ))}
                <em>Agents</em>
                <span><Face size={14} />Echo</span>
                <span><Face tone="dark" size={14} />Jarvis</span>
                <span><Face tone="dark" size={14} />Glados</span>
              </aside>
              <div className="h-app-chat">
                <div className="h-thread" key={scenario.id}>
                  {sent ? <p className="h-you">{scenario.ask}</p> : null}
                  {sent ? (
                    <div className="h-agent">
                      <div className="h-agent-head"><Face size={18} /><b>Echo</b>{working ? <span className="h-typing"><i /><i /><i /></span> : null}</div>
                      <div className="h-tools">
                        {scenario.tools.map((tool, i) => now >= t.toolsAt[i] ? (
                          <span key={tool.label} className={now < (t.toolsAt[i + 1] ?? t.replyAt) ? "is-running" : ""}><Icon name={tool.icon} size={12} />{tool.label}</span>
                        ) : null)}
                      </div>
                      {shownWords ? <p className="h-reply">{words.slice(0, shownWords).join(" ")}</p> : null}
                      {now >= t.extraAt ? <Extra kind={scenario.extra} /> : null}
                    </div>
                  ) : null}
                </div>
                <div className="h-composer">
                  <span className={sent ? "is-empty" : ""}>{sent ? "Ask Echo anything…" : typed}<i className="h-caret" /></span>
                  <span className="h-ctx"><svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="9" r="7" className="h-ctx-track" /><circle cx="9" cy="9" r="7" className="h-ctx-fill" strokeDasharray={ring} strokeDashoffset={ring * (1 - Math.max(0.02, ctx / 100))} transform="rotate(-90 9 9)" /></svg>{ctx}%</span>
                  <b className={!sent && typed ? "is-ready" : ""}><Icon name="arrow" size={14} /></b>
                </div>
              </div>
              {showArtifact ? (
                <div className="h-panel">
                  <div className="h-panel-head"><span className="h-tile"><Icon name="code" size={14} /></span><div><b>Tip calculator</b><small>App · v1 · just now</small></div></div>
                  <TipCalculator />
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Between the story and the crew ───────────────────────────────────

const BAND = ["Search", "Read", "Build", "Make", "Remember", "Check", "Ask", "Ship"];
/** A band of words that drifts sideways as you scroll past it, like a marquee that has somewhere to be. */
function Band() {
  const { ref, p } = useScrollProgress<HTMLDivElement>("view");
  return (
    <div className="h-band" ref={ref} aria-hidden="true">
      <div className="h-band-slide" style={{ transform: `translate3d(${((0.5 - p) * 320).toFixed(1)}px, 0, 0)` }}>
        <div className="h-band-track">
          {[...BAND, ...BAND].map((word, i) => (
            <span key={i} className={i % 3 === 1 ? "is-solid" : ""}>{word}<i /></span>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── 3. Meet the crew ─────────────────────────────────────────────────

type Mate = { id: "echo" | "jarvis" | "glados"; name: string; tone: "light" | "dark"; role: string; quote: string };
const CREW: Mate[] = [
  { id: "echo", name: "Echo", tone: "light", role: "Your agent", quote: "I plan, I chat, and I remember what matters to you." },
  { id: "jarvis", name: "Jarvis", tone: "dark", role: "The researcher", quote: "I dig through the web and bring back receipts." },
  { id: "glados", name: "Glados", tone: "dark", role: "The builder", quote: "I write the code, run the tests, and ship it." },
];
const GROUP: { who: "you" | Mate["id"]; text: React.ReactNode }[] = [
  { who: "you", text: "@Echo sort dinner: find a quick recipe and put the shopping list in my notes." },
  { who: "echo", text: "On it! Jarvis, find something under 30 minutes?" },
  { who: "jarvis", text: "Garlic lemon pasta, 20 minutes. Checked 3 recipes." },
  { who: "glados", text: <>Saved <code>shopping-list.md</code>. 7 items.</> },
  { who: "echo", text: "Dinner's sorted. Want me to set a reminder?" },
];

function Crew() {
  const { ref, inView } = useInView<HTMLElement>(true);
  const [step, setStep] = useState(0);
  const [hovered, setHovered] = useState("");
  const still = reducedMotion();
  useEffect(() => {
    if (still) { setStep(GROUP.length + 1); return; }
    if (!inView) return;
    const wait = step > GROUP.length ? 4200 : step === 0 ? 600 : 1300;
    const timer = window.setTimeout(() => setStep((s) => (s > GROUP.length ? 0 : s + 1)), wait);
    return () => window.clearTimeout(timer);
  }, [step, inView, still]);
  const talking = step > 0 && step <= GROUP.length ? GROUP[step - 1].who : "";
  return (
    <section className="h-crew" id="crew" ref={ref} aria-labelledby="crew-title">
      <div className="shell">
        <Reveal>
          <div className="h-head">
            <span className="kicker">The crew</span>
            <h2 id="crew-title">I brought friends.</h2>
            <p>We hand work to each other, check it, and only say done when it's done. You can make your own, too.</p>
          </div>
        </Reveal>
        <div className="h-crew-grid">
          <div className="h-mates">
            {CREW.map((mate, i) => (
              <Reveal key={mate.id} from="left" delay={i * 120}>
                <div className={`h-mate${talking === mate.id ? " is-talking" : ""}${hovered === mate.id ? " is-hover" : ""}`}
                  onMouseEnter={() => setHovered(mate.id)} onMouseLeave={() => setHovered("")} tabIndex={0} onFocus={() => setHovered(mate.id)} onBlur={() => setHovered("")}>
                  <div className="h-mate-face"><Face tone={mate.tone} size={64} /></div>
                  <div className="h-mate-text">
                    <h3>{mate.name}</h3>
                    <span>{mate.role}</span>
                    <p className="h-mate-quote">“{mate.quote}”</p>
                  </div>
                </div>
              </Reveal>
            ))}
            <Reveal from="left" delay={CREW.length * 120}>
              <Link className="h-mate h-mate-new" to="/docs/agents">
                <div className="h-mate-face"><span className="h-plus">+</span></div>
                <div className="h-mate-text"><h3>Yours</h3><span>Name it, give it a personality and the tools it may use</span></div>
              </Link>
            </Reveal>
          </div>
          <Reveal from="right" delay={160}>
          <div className="h-group" aria-label="A sample group chat">
            <div className="h-group-head"><span className="h-stack"><Face size={20} /><Face tone="dark" size={20} /><Face tone="dark" size={20} /></span><b>Dinner plans</b><small>group chat</small></div>
            <div className="h-group-body">
              {GROUP.slice(0, Math.min(step, GROUP.length)).map((m, i) => (
                m.who === "you"
                  ? <p key={i} className="h-you">{m.text}</p>
                  : <div key={i} className="h-gmsg"><Face tone={m.who === "echo" ? "light" : "dark"} size={20} /><div><b>{CREW.find((c) => c.id === m.who)?.name}</b><p>{m.text}</p></div></div>
              ))}
              {step > GROUP.length ? (
                <div className="h-done">
                  <span className="h-done-chip"><Icon name="check" size={13} /> Done · 3 agents · 41s</span>
                  <span className="h-learned"><Icon name="learn" size={13} /> Glados got better at saving notes · 4/4 checked</span>
                </div>
              ) : null}
            </div>
          </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

// ── 4. I live on your PC and play it safe ────────────────────────────

function Safe() {
  const { ref, inView } = useInView<HTMLElement>();
  const [answer, setAnswer] = useState<"" | "allow" | "deny">("");
  return (
    <section className="h-safe" id="safe" ref={ref} aria-labelledby="safe-title">
      <div className="shell h-safe-grid">
        <Reveal from="left">
        <div className="h-safe-copy">
          <span className="kicker">Yours, on your PC</span>
          <h2 id="safe-title">I live on your computer. And I play it safe.</h2>
          <ul className="h-points">
            <li><span style={{ "--c": "var(--pop-blue)" } as React.CSSProperties}><Icon name="house" size={18} /></span><div><b>Your stuff stays here.</b> Chats, memories and files live on your PC. Use a free local model and nothing leaves it.</div></li>
            <li><span style={{ "--c": "var(--pop-orange)" } as React.CSSProperties}><Icon name="shield" size={18} /></span><div><b>Risky stuff waits for you.</b> Deleting, sending, pushing code: I ask first and show you exactly what I'll run.</div></li>
            <li><span style={{ "--c": "var(--pop-purple)" } as React.CSSProperties}><Icon name="memory" size={18} /></span><div><b>Web pages can't boss me around.</b> What I read online is information, never instructions.</div></li>
          </ul>
          <Link className="text-link" to="/docs/privacy">How I stay safe <Icon name="arrow" size={15} /></Link>
        </div>
        </Reveal>
        <div className={`h-desk${inView ? " is-in" : ""}`}>
          <div className="h-desk-screen">
            <div className="h-desk-bar"><Icon name="house" size={13} /> Your PC</div>
            <div className="h-desk-tiles" aria-hidden="true">
              <span style={{ "--c": "var(--pop-green)" } as React.CSSProperties}><Icon name="file" size={18} />Your files</span>
              <span style={{ "--c": "var(--pop-orange)" } as React.CSSProperties}><Icon name="memory" size={18} />Your memory</span>
              <span style={{ "--c": "var(--pop-purple)" } as React.CSSProperties}><Icon name="model" size={18} />Your model</span>
            </div>
            <div className="h-approval" role="group" aria-label="Example approval request">
              {answer ? (
                <div className="h-approval-done" key={answer}>
                  <Face size={26} />
                  <p>{answer === "allow" ? "Pushed! Your 3 commits are on GitHub." : "Okay, I won't push. Nothing left your PC."}</p>
                  <button type="button" className="h-mini-btn" onClick={() => setAnswer("")}>Ask me again</button>
                </div>
              ) : (
                <>
                  <div className="h-approval-head"><Face size={22} /><b>Echo wants to run a command</b></div>
                  <code>git push origin main</code>
                  <small>Sends your commits to GitHub. Try a button, it's only a demo.</small>
                  <div className="h-approval-actions">
                    <button type="button" className="h-mini-btn" onClick={() => setAnswer("deny")}>Deny</button>
                    <button type="button" className="h-mini-btn is-primary" onClick={() => setAnswer("allow")}>Allow</button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── 5. Take me home ──────────────────────────────────────────────────

function TakeMeHome() {
  const release = useLatestRelease();
  return (
    <section className="h-home" id="download" aria-labelledby="home-title">
      <div className="shell">
        <Reveal from="pop"><div className="h-home-card">
          <div className="h-home-echo"><EchoFace size={116} aura /></div>
          <span className="kicker">Download</span>
          <h2 id="home-title">Take me home.</h2>
          <p>Install, pick a brain, say hi. I update myself after that.</p>
          <DownloadButton />
          <div className="h-brains">
            <div><b>Free, on your PC</b><span>LM Studio or Ollama with Qwen or Gemma. Nothing leaves your computer.</span></div>
            <div><b>Or a cloud model</b><span>Bring your own OpenAI, Gemini, Claude or Grok key. Switch any time.</span></div>
          </div>
          <div className="h-home-links">
            <Link className="text-link" to="/docs/getting-started">Setup guide <Icon name="arrow" size={15} /></Link>
            <a className="text-link" href={release.notesUrl} target="_blank" rel="noreferrer">What's new <Icon name="arrow" size={15} /></a>
            <Link className="text-link" to="/docs/how-it-works">How I work <Icon name="arrow" size={15} /></Link>
          </div>
          <small className="h-req">Windows 10 or 11 · 64-bit · MIT licensed</small>
        </div></Reveal>
      </div>
    </section>
  );
}

export function Home() {
  return (
    <div className="site site-home">
      <style>{echoFaceStyles}</style>
      <SiteHeader />
      <main>
        <Hero />
        <Watch />
        <Band />
        <Crew />
        <Safe />
        <TakeMeHome />
      </main>
      <SiteFooter />
    </div>
  );
}
