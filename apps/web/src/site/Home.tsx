import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { animate, motion, useMotionValue, useMotionValueEvent, useScroll, useTransform, type MotionValue } from "framer-motion";
import { EchoFace, echoFaceStyles } from "../components/EchoFace";
import { DownloadButton, Face, Icon, SiteFooter, SiteHeader, type IconName } from "./Chrome";
import { GITHUB_URL, useLatestRelease } from "./release";
import "./site.css";
import "./home.css";

/**
 * The front page is one scroll story in Echo's voice:
 * 1. Echo introduces himself and the product (a pinned, Apple-style reveal)
 * 2. Watch me work (a pinned chapter: each scroll step plays one scenario)
 * 3. Meet the crew (a group chat)
 * 4. I live on your computer, and I play it safe (a pinned approval moment)
 * 5. Take me home (download).
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

/**
 * Scroll progress through a tall section whose inner box sticks to the screen.
 * With reduced motion the section is not pinned and shows its finished state.
 */
function usePinned<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const still = reducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });
  const finished = useMotionValue(1);
  const p: MotionValue<number> = still ? finished : scrollYProgress;
  return { ref, p, still };
}

/** Maps a slice of scroll progress to 0→1, held at the ends. */
const useBeat = (p: MotionValue<number>, from: number, to: number) => useTransform(p, [from, to], [0, 1]);

/** Text that rises and fades in as its beat of scroll plays. */
function Reveal({ v, className, children }: { v: MotionValue<number>; className?: string; children: React.ReactNode }) {
  const y = useTransform(v, [0, 1], [34, 0]);
  return <motion.div className={className} style={{ opacity: v, y }}>{children}</motion.div>;
}

// ── 1. Echo introduces himself ───────────────────────────────────────

function Intro() {
  const { ref, p, still } = usePinned<HTMLElement>();
  // The blue line draws through "Echo" once the page opens, a moment after the wordmark appears.
  const swoosh = useMotionValue(0);
  const trail = useMotionValue(0);
  useEffect(() => {
    if (still) { swoosh.set(1); trail.set(1); return; }
    const first = animate(swoosh, 1, { duration: 1.3, delay: 0.25, ease: [0.65, 0, 0.2, 1] });
    const second = animate(trail, 1, { duration: 1.1, delay: 0.9, ease: [0.65, 0, 0.2, 1] });
    return () => { first.stop(); second.stop(); };
  }, [still, swoosh, trail]);
  // Beat 2: the wordmark gives way to Echo's hello.
  const brandOut = useTransform(p, [0.36, 0.5], [1, 0]);
  const brandLift = useTransform(p, [0.36, 0.5], [0, -60]);
  const helloIn = useBeat(p, 0.44, 0.56);
  // Beat 3: the copy arrives line by line.
  const l1 = useBeat(p, 0.56, 0.64);
  const l2 = useBeat(p, 0.64, 0.72);
  const l3 = useBeat(p, 0.74, 0.82);
  const cta = useBeat(p, 0.84, 0.92);
  return (
    <section className={`pinned intro${still ? " is-still" : ""}`} ref={ref} style={{ height: still ? undefined : "300vh" }} aria-label="Echo Speak introduction">
      <div className="pin intro-pin">
        <motion.div className="intro-brand" style={{ opacity: brandOut, y: brandLift }}>
          <div className="intro-face"><EchoFace size="min(34vh, 300px)" aura mode="idle" /></div>
          <h1 className="intro-word" aria-label="Echo Speak">
            <span className="intro-echo" aria-hidden="true">
              Echo
              <svg className="intro-swoosh" viewBox="0 0 200 60" preserveAspectRatio="none" aria-hidden="true">
                <defs>
                  <linearGradient id="intro-blue" x1="0" x2="1" y1="0" y2="0">
                    <stop offset="0" stopColor="#8dc0ff" />
                    <stop offset="0.55" stopColor="#3d8bff" />
                    <stop offset="1" stopColor="#2c73e8" />
                  </linearGradient>
                </defs>
                <motion.path d="M-6 46 C 34 4, 66 60, 108 26 S 178 2, 210 24" fill="none" stroke="url(#intro-blue)" strokeWidth={12} strokeLinecap="round" vectorEffect="non-scaling-stroke" pathLength={swoosh} />
                <motion.path d="M-6 16 C 40 52, 84 -6, 128 38 S 186 50, 210 34" fill="none" stroke="#3d8bff" strokeOpacity={0.6} strokeWidth={4} strokeLinecap="round" vectorEffect="non-scaling-stroke" pathLength={trail} />
              </svg>
            </span>{" "}Speak
          </h1>
          <p className="intro-tag">Your personal agent. It lives on your computer.</p>
        </motion.div>
        <motion.div className="intro-hello" style={{ opacity: helloIn }}>
          <div className="intro-hello-face"><EchoFace size="min(30vh, 260px)" aura mode="idle" /></div>
          <div className="intro-hello-copy">
            <Reveal v={l1}><h2>Hi, I'm Echo.</h2></Reveal>
            <Reveal v={l2}><h3>I live on your computer.</h3></Reveal>
            <Reveal v={l3}><p>I look things up, build things and make things. I ask before anything risky, and your files stay with you.</p></Reveal>
            <Reveal v={cta}>
              <div className="h-actions">
                <DownloadButton />
                <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer"><Icon name="github" size={18} /> View on GitHub</a>
              </div>
            </Reveal>
          </div>
        </motion.div>
        <span className="intro-scroll" aria-hidden="true">Scroll</span>
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

function Watch() {
  const { ref, p, still } = usePinned<HTMLElement>();
  // Scroll position across the three scenarios: 0–1 each, in order.
  const [pos, setPos] = useState(0);
  useMotionValueEvent(p, "change", (v) => setPos(Math.min(SCENARIOS.length - 0.001, Math.max(0, v * SCENARIOS.length))));
  const active = Math.floor(pos);
  const scenario = SCENARIOS[active];
  const t = timeline(scenario);
  const elapsed = (pos - active) * t.end;
  const typed = scenario.ask.slice(0, Math.max(0, Math.floor((elapsed - 450) / 28)));
  const sent = elapsed >= t.sent;
  const words = scenario.reply.split(" ");
  const shownWords = elapsed >= t.replyAt ? Math.min(words.length, Math.floor((elapsed - t.replyAt) / 55) + 1) : 0;
  const ctx = Math.round(scenario.ctx * Math.min(1, elapsed / t.extraAt));
  const working = sent && elapsed < t.replyAt;
  const showArtifact = scenario.extra === "artifact" && elapsed >= t.extraAt;
  const ring = 2 * Math.PI * 7;
  // Each tab jumps to the scroll position where its scenario starts.
  const jump = (i: number) => {
    const el = ref.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    window.scrollTo({ top: top + (i / SCENARIOS.length) * (el.offsetHeight - window.innerHeight) + 2, behavior: "smooth" });
  };
  return (
    <section className={`pinned watch-pinned${still ? " is-still" : ""}`} id="watch" ref={ref} style={{ height: still ? undefined : "340vh" }} aria-labelledby="watch-title">
      <div className="pin">
        <div className="shell">
          <div className="h-head">
            <span className="kicker">Watch me work</span>
            <h2 id="watch-title">Ask once. I'll take it from there.</h2>
          </div>
          <div className="h-tabs" role="tablist" aria-label="What I can do">
            {SCENARIOS.map((s, i) => (
              <button key={s.id} type="button" role="tab" aria-selected={i === active} className={i === active ? "is-on" : ""} onClick={() => jump(i)}>
                <Icon name={s.icon} size={16} /> {s.tab}
              </button>
            ))}
          </div>
          <div className={`h-app${showArtifact ? " has-panel" : ""}`} aria-label="A sample EchoSpeak chat">
            <div className="h-app-bar"><Face size={16} /><b>EchoSpeak</b><span>live demo</span></div>
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
                        {scenario.tools.map((tool, i) => elapsed >= t.toolsAt[i] ? (
                          <span key={tool.label} className={elapsed < (t.toolsAt[i + 1] ?? t.replyAt) ? "is-running" : ""}><Icon name={tool.icon} size={12} />{tool.label}</span>
                        ) : null)}
                      </div>
                      {shownWords ? <p className="h-reply">{words.slice(0, shownWords).join(" ")}</p> : null}
                      {elapsed >= t.extraAt ? <Extra kind={scenario.extra} /> : null}
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
        <div className="h-head">
          <span className="kicker">The crew</span>
          <h2 id="crew-title">I brought friends.</h2>
          <p>We hand work to each other, check it, and only say done when it's done. You can make your own, too.</p>
        </div>
        <div className="h-crew-grid">
          <div className="h-mates">
            {CREW.map((mate) => (
              <div key={mate.id} className={`h-mate${talking === mate.id ? " is-talking" : ""}${hovered === mate.id ? " is-hover" : ""}`}
                onMouseEnter={() => setHovered(mate.id)} onMouseLeave={() => setHovered("")} tabIndex={0} onFocus={() => setHovered(mate.id)} onBlur={() => setHovered("")}>
                <div className="h-mate-face"><Face tone={mate.tone} size={64} /></div>
                <div className="h-mate-text">
                  <h3>{mate.name}</h3>
                  <span>{mate.role}</span>
                  <p className="h-mate-quote">“{mate.quote}”</p>
                </div>
              </div>
            ))}
            <Link className="h-mate h-mate-new" to="/docs/agents">
              <div className="h-mate-face"><span className="h-plus">+</span></div>
              <div className="h-mate-text"><h3>Yours</h3><span>Name it, give it a personality and the tools it may use</span></div>
            </Link>
          </div>
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
        </div>
      </div>
    </section>
  );
}

// ── 4. I live on your computer, and I play it safe ───────────────────

const FACTS: { icon: IconName; color: string; title: string; body: string }[] = [
  { icon: "house", color: "var(--pop-blue)", title: "Your stuff stays here.", body: "Chats, memories and files live on your PC. Use a free local model and nothing leaves it." },
  { icon: "shield", color: "var(--pop-orange)", title: "Risky things wait for you.", body: "Deleting, sending, pushing code: I ask first and show you exactly what I'll run." },
  { icon: "memory", color: "var(--pop-purple)", title: "Web pages can't boss me.", body: "What I read online is information, never instructions." },
];

function Safe() {
  const { ref, p, still } = usePinned<HTMLElement>();
  const headIn = useBeat(p, 0.02, 0.14);
  // The approval card arrives, Echo asks, and "Allow" is pressed.
  const cardIn = useBeat(p, 0.14, 0.26);
  const cardOpacity = useTransform(p, [0.14, 0.26, 0.58, 0.63], [0, 1, 1, 0]);
  const cardY = useTransform(cardIn, [0, 1], [48, 0]);
  const pressScale = useTransform(p, [0.46, 0.5, 0.54], [1, 0.9, 1]);
  const done = useBeat(p, 0.62, 0.68);
  const facts = [useBeat(p, 0.72, 0.8), useBeat(p, 0.8, 0.88), useBeat(p, 0.88, 0.96)];
  return (
    <section className={`pinned safe${still ? " is-still" : ""}`} id="safe" ref={ref} style={{ height: still ? undefined : "300vh" }} aria-labelledby="safe-title">
      <div className="pin safe-pin">
        <Reveal v={headIn} className="safe-head">
          <span className="kicker">Yours, on your PC</span>
          <h2 id="safe-title">I live on your computer.<br />And I play it safe.</h2>
        </Reveal>
        <div className="safe-stage">
          <motion.div className="safe-card" style={{ opacity: cardOpacity, y: cardY }} aria-label="Example approval request">
            <div className="safe-card-head"><Face size={24} /><b>Echo wants to run a command</b></div>
            <code>git push origin main</code>
            <div className="safe-card-actions">
              <span className="h-mini-btn">Deny</span>
              <motion.span className="h-mini-btn is-primary" style={{ scale: pressScale }}>Allow</motion.span>
            </div>
          </motion.div>
          <motion.p className="safe-done" style={{ opacity: done }}>
            <Face size={26} /> Pushed. Nothing else left your PC.
          </motion.p>
        </div>
        <div className="safe-facts">
          {FACTS.map((fact, i) => (
            <Reveal key={fact.title} v={facts[i]} className="safe-fact">
              <span style={{ "--c": fact.color } as React.CSSProperties}><Icon name={fact.icon} size={20} /></span>
              <b>{fact.title}</b>
              <p>{fact.body}</p>
            </Reveal>
          ))}
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
        <div className="h-home-card">
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
        </div>
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
        <Intro />
        <Watch />
        <Crew />
        <Safe />
        <TakeMeHome />
      </main>
      <SiteFooter />
    </div>
  );
}

