import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MotionConfig, motion, useMotionValue, useMotionValueEvent, useScroll, type MotionValue } from "framer-motion";
import { EchoFace, echoFaceStyles } from "../components/EchoFace";
import { DownloadButton, Face, Icon, SiteFooter, SiteHeader, useScrollTo, type IconName } from "./Chrome";
import { GITHUB_URL, useLatestRelease } from "./release";
import { Workspace } from "./Workspace";
import "./site.css";
import "./home.css";

/**
 * The front page: "The Living Workspace". It shows EchoSpeak working instead of
 * describing it. Hero → four answers about any agent → Watch a run (the
 * interactive workspace) → How a run works (one pinned loop) → agents with
 * identity and memory → a team, A2A and learning → safety → download.
 * Claims stay inside what the product does today; learning is labelled Preview.
 */

const reducedMotion = () => typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

/** Rises into place once, the first time it scrolls into view. */
function Rise({ children, className, delay = 0, as = "div" }: { children: React.ReactNode; className?: string; delay?: number; as?: "div" | "li" }) {
  const Tag = as === "li" ? motion.li : motion.div;
  return (
    <Tag className={className} initial={{ opacity: 0, y: 26 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: 0.25 }}
      transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}>
      {children}
    </Tag>
  );
}

function SectionHead({ kicker, title, children, id }: { kicker: string; title: React.ReactNode; children?: React.ReactNode; id: string }) {
  return (
    <Rise className="lw-head">
      <span className="kicker">{kicker}</span>
      <h2 id={id}>{title}</h2>
      {children ? <p>{children}</p> : null}
    </Rise>
  );
}

// ── Hero ─────────────────────────────────────────────────────────────

const HERO_STEPS = [
  { label: "Searched “best budget streaming mic”", sub: "14 results · read 2 pages" },
  { label: "Read soundguys.com", sub: "Read 3,840 words" },
  { label: "Checked prices for 3 mics", sub: "3 products · prices from today" },
];

/** Echo with a short trace of finished steps under him, replayed slowly. */
function HeroTrace() {
  const still = reducedMotion();
  const [shown, setShown] = useState(still ? HERO_STEPS.length : 0);
  useEffect(() => {
    if (still) return;
    const wait = shown >= HERO_STEPS.length ? 5200 : shown === 0 ? 900 : 1500;
    const timer = window.setTimeout(() => setShown((n) => (n >= HERO_STEPS.length ? 0 : n + 1)), wait);
    return () => window.clearTimeout(timer);
  }, [shown, still]);
  const running = shown < HERO_STEPS.length;
  return (
    <div className="lw-hero-visual" aria-hidden="true">
      <div className="lw-hero-echo"><EchoFace size="clamp(132px, 15vw, 188px)" aura mode="idle" /></div>
      <div className="lw-hero-card">
        <div className="lw-hero-card-head"><Face size={18} /><b>Echo</b><span data-state={running ? "running" : "done"}>{running ? "working" : "done · 6.4s"}</span></div>
        <ol>
          {HERO_STEPS.map((step, i) => (
            <li key={step.label} data-state={i < shown ? "done" : i === shown ? "running" : "pending"}>
              <span className="lw-dot" />
              <span><b>{step.label}</b><small>{i < shown ? step.sub : i === shown ? "working…" : ""}</small></span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function Hero() {
  const scrollTo = useScrollTo();
  return (
    <section className="lw-hero" id="top" aria-labelledby="hero-title">
      <div className="shell lw-hero-grid">
        <div className="lw-hero-copy">
          <span className="kicker">A personal agent workspace for Windows</span>
          <h1 id="hero-title">
            Meet{" "}
            <span className="lw-echo">
              Echo
              <svg className="lw-swoosh" viewBox="0 0 200 60" preserveAspectRatio="none" aria-hidden="true">
                <defs>
                  <linearGradient id="lw-blue" x1="0" x2="1" y1="0" y2="0">
                    <stop offset="0" stopColor="#8dc0ff" /><stop offset="0.55" stopColor="#3d8bff" /><stop offset="1" stopColor="#2c73e8" />
                  </linearGradient>
                </defs>
                <motion.path d="M-6 46 C 34 4, 66 60, 108 26 S 178 2, 210 24" fill="none" stroke="url(#lw-blue)" strokeWidth={8} strokeLinecap="round" vectorEffect="non-scaling-stroke"
                  initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.3, delay: 0.35, ease: [0.65, 0, 0.2, 1] }} />
              </svg>
            </span>
            .<br />
            <span className="lw-soft">Agents that show their work.</span>
          </h1>
          <p className="lw-lede">EchoSpeak runs a small team of AI agents on your PC. They plan, use real tools, ask before anything risky, and leave every step open for you to inspect.</p>
          <div className="lw-actions">
            <DownloadButton />
            <button type="button" className="btn btn-ghost" onClick={() => scrollTo("run")}>Watch a run <Icon name="down" size={17} /></button>
          </div>
          <p className="lw-proof"><span>Free and open source</span><span>Local models or your own key</span><a href={GITHUB_URL} target="_blank" rel="noreferrer"><Icon name="github" size={15} /> GitHub</a></p>
        </div>
        <HeroTrace />
      </div>
    </section>
  );
}

// ── Four answers ─────────────────────────────────────────────────────

function Answers() {
  return (
    <section className="lw-answers" aria-labelledby="answers-title">
      <div className="shell">
        <SectionHead kicker="Not a chatbot" id="answers-title" title="Four things every agent should tell you.">
          A chat box answers questions. A workspace does the work, and it should always be clear what's happening.
        </SectionHead>
        <ol className="lw-answer-grid">
          <Rise as="li" className="lw-answer">
            <h3>What will it do?</h3><p>It says the plan before it starts.</p>
            <div className="lw-mini lw-mini-plan" aria-hidden="true">
              <span data-state="done"><i />Read the failing test</span><span data-state="running"><i />Fix calc.py</span><span><i />Run the tests again</span>
            </div>
          </Rise>
          <Rise as="li" className="lw-answer" delay={0.08}>
            <h3>When does it ask?</h3><p>Before deleting, sending, pushing or anything risky.</p>
            <div className="lw-mini lw-mini-gate" aria-hidden="true"><b>Allow this command?</b><code>git push origin main</code><span><em>Deny</em><em className="is-primary">Allow</em></span></div>
          </Rise>
          <Rise as="li" className="lw-answer" delay={0.16}>
            <h3>How do I stop it?</h3><p>One click, or press Esc.</p>
            <div className="lw-mini lw-mini-stop" aria-hidden="true"><Face size={16} /><span>Reading rtings.com</span><em>0:07</em><b>■ Stop</b></div>
          </Rise>
          <Rise as="li" className="lw-answer" delay={0.24}>
            <h3>What did it do?</h3><p>Every step stays open to inspect.</p>
            <div className="lw-mini lw-mini-step" aria-hidden="true"><Icon name="check" size={12} /><span><b>Ran `pytest -q`</b><small>Tests: 12 passed</small></span><em>2.2s</em></div>
          </Rise>
        </ol>
      </div>
    </section>
  );
}

// ── Watch a run ──────────────────────────────────────────────────────

function WatchARun() {
  return (
    <section className="lw-run" id="run" aria-labelledby="run-title">
      <div className="shell">
        <SectionHead kicker="Watch a run" id="run-title" title="Give it a task. Watch the work happen.">
          Pick a task. Echo plans it, hands parts to teammates, uses tools and stops for your OK before anything risky. Click any step to inspect it.
        </SectionHead>
        <Rise><Workspace /></Rise>
        <p className="lw-footnote">Example runs, written to match the app's real step labels. In the app, the same steps stream live from your own agents.</p>
      </div>
    </section>
  );
}

// ── How a run works ──────────────────────────────────────────────────

const STAGES: { name: string; icon: IconName; title: string; body: string; chips: string[]; example: string[]; preview?: boolean }[] = [
  { name: "Recall", icon: "memory", title: "Remembers what matters.", body: "Before answering, the agent pulls the memories that fit this request, like your preferences and your projects. It keeps lasting facts, not small talk.", chips: ["Memory"], example: ["Recalled: you're vegetarian", "Recalled: weeknight dinners under 30 minutes"] },
  { name: "Plan", icon: "spark", title: "Says the plan first.", body: "For anything with several steps, it opens with one sentence on what it's about to do, then gets started.", chips: ["Plan"], example: ["I'll compare three mics by reading reviews and checking current prices."] },
  { name: "Act", icon: "terminal", title: "Uses real tools.", body: "Web search that reads the top pages, files, the terminal, git and GitHub, images and prices. Each step shows a live line while it runs.", chips: ["Web", "Files", "Terminal", "Git"], example: ["Searching “best budget streaming mic”", "14 results · reading the top 2", "Reading rtings.com (1 of 2)"] },
  { name: "Check", icon: "shield", title: "Checks before it says done.", body: "It runs the tests, opens what it built, and reads sources instead of snippets. Risky steps stop and wait for your OK.", chips: ["Tests", "Approvals"], example: ["Ran `pytest -q` · Tests: 12 passed", "Waiting for your OK: git push origin main"] },
  { name: "Answer", icon: "chat", title: "Answers with receipts.", body: "The answer comes with its sources, files and diffs, and every step stays open to inspect.", chips: ["Sources", "Artifacts"], example: ["Comparison table · 3 sources", "1 file changed · 12 tests passing"] },
  { name: "Learn", icon: "learn", title: "Gets better from what worked.", body: "Finished work is graded. A lesson is kept only after it checks out, and it can never change permissions, approvals, tools or settings.", chips: ["Preview"], example: ["Graded: checks passed", "Lesson kept: run the tests before pushing", "Can't change: permissions, approvals, tools"], preview: true },
];

function usePinned<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const still = reducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });
  const finished = useMotionValue(1);
  const p: MotionValue<number> = still ? finished : scrollYProgress;
  return { ref, p, still };
}

function Loop() {
  const { ref, p, still } = usePinned<HTMLElement>();
  const [active, setActive] = useState(0);
  useMotionValueEvent(p, "change", (v) => setActive(Math.min(STAGES.length - 1, Math.max(0, Math.floor(v * STAGES.length)))));
  const stage = STAGES[active];
  return (
    <section className={`lw-loop${still ? " is-still" : ""}`} id="how" ref={ref} style={{ height: still ? undefined : `${STAGES.length * 60 + 100}vh` }} aria-labelledby="how-title">
      <div className="lw-pin">
        <div className="shell">
          <div className="lw-head">
            <span className="kicker">How a run works</span>
            <h2 id="how-title">One loop, every time.</h2>
          </div>
          <ol className="lw-track" style={{ "--p": active / (STAGES.length - 1) } as React.CSSProperties}>
            {STAGES.map((s, i) => (
              <li key={s.name} data-state={i < active ? "past" : i === active ? "on" : "next"} aria-current={i === active ? "step" : undefined}>
                <span className="lw-station"><Icon name={s.icon} size={20} /></span>
                <b>{s.name}</b>
              </li>
            ))}
          </ol>
          <div className="lw-stage-wrap" key={stage.name}>
            <div className="lw-stage">
              <span className="lw-stage-num">{String(active + 1).padStart(2, "0")} / {String(STAGES.length).padStart(2, "0")}</span>
              <h3>{stage.title}{stage.preview ? <em className="lw-badge">Preview</em> : null}</h3>
              <p>{stage.body}</p>
              <div className="lw-chips">{stage.chips.map((c) => <span key={c}>{c}</span>)}</div>
            </div>
            <div className="lw-example" aria-label={`Example: ${stage.name}`}>
              <span><Face size={16} /> In a real run</span>
              {stage.example.map((line, i) => <p key={line} style={{ animationDelay: `${0.15 + i * 0.18}s` }}>{line}</p>)}
            </div>
          </div>
          <ol className="lw-stage-list">
            {STAGES.map((s) => (
              <li key={s.name}><span className="lw-station"><Icon name={s.icon} size={18} /></span><div><h3>{s.name}: {s.title}{s.preview ? <em className="lw-badge">Preview</em> : null}</h3><p>{s.body}</p></div></li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

// ── Agents with identity and memory ──────────────────────────────────

const PEOPLE: { name: string; tone: "light" | "dark"; role: string; uses: string[]; remembers: string[] }[] = [
  { name: "Echo", tone: "light", role: "Your agent. Plans, chats and hands work out.", uses: ["Everything you allow"], remembers: ["Prefers short answers", "Working on EchoSpeak"] },
  { name: "Jarvis", tone: "dark", role: "The researcher. Reads sources, checks prices.", uses: ["Web search", "Reading pages", "Prices"], remembers: ["Trusts rtings.com for audio"] },
  { name: "Glados", tone: "dark", role: "The builder. Writes code and runs the tests.", uses: ["Files", "Terminal", "Git"], remembers: ["Tests run with pytest"] },
];

function Agents() {
  return (
    <section className="lw-agents" id="agents" aria-labelledby="agents-title">
      <div className="shell">
        <SectionHead kicker="Identity and memory" id="agents-title" title="Agents with a name, a job and a memory.">
          Each agent keeps its own personality, its own tools and what it has learned about you. Make as many as you like.
        </SectionHead>
        <div className="lw-people">
          {PEOPLE.map((p, i) => (
            <Rise key={p.name} className="lw-person" delay={i * 0.06}>
              <Face tone={p.tone} size={52} />
              <h3>{p.name}</h3>
              <p>{p.role}</p>
              <dl>
                <dt>Can use</dt><dd>{p.uses.map((u) => <span key={u}>{u}</span>)}</dd>
                <dt>Remembers</dt><dd>{p.remembers.map((r) => <span key={r} className="is-mem">{r}</span>)}</dd>
              </dl>
            </Rise>
          ))}
          <Rise className="lw-person lw-person-new" delay={0.18}>
            <span className="lw-plus" aria-hidden="true">+</span>
            <h3>Yours</h3>
            <p>Name it, give it a personality, and choose the tools it may use.</p>
            <Link className="text-link" to="/docs/agents">Make an agent <Icon name="arrow" size={15} /></Link>
          </Rise>
        </div>
        <Rise className="lw-memory">
          <div><span className="kicker">Memory</span><h3>Keeps what lasts. Skips the small talk.</h3><p>Memories live on your PC. You can see, pin, change or delete every one in Settings.</p></div>
          <ul aria-label="Examples of what memory keeps">
            <li data-kept="true"><Icon name="check" size={14} />You're vegetarian</li>
            <li data-kept="true"><Icon name="check" size={14} />The calculator repo uses pytest</li>
            <li data-kept="false"><span aria-hidden="true">×</span>Asked about the weather at 3pm</li>
            <li data-kept="false"><span aria-hidden="true">×</span>Said thanks</li>
          </ul>
        </Rise>
      </div>
    </section>
  );
}

// ── A team, A2A and learning ─────────────────────────────────────────

const GROUP: { who: "you" | "echo" | "jarvis" | "glados"; text: React.ReactNode }[] = [
  { who: "you", text: "@Echo sort dinner: a quick recipe, and put the shopping list in my notes." },
  { who: "echo", text: "On it. Jarvis, something vegetarian under 30 minutes?" },
  { who: "jarvis", text: "Garlic lemon pasta, 20 minutes. I read 3 recipes." },
  { who: "glados", text: <>Saved <code>shopping-list.md</code>. 7 items.</> },
  { who: "echo", text: "Dinner's sorted. Want a reminder at 5?" },
];
const NAMES = { echo: "Echo", jarvis: "Jarvis", glados: "Glados" } as const;

function GroupChat() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [step, setStep] = useState(0);
  const [inView, setInView] = useState(false);
  const still = reducedMotion();
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") { setInView(true); return; }
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.3 });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (still) { setStep(GROUP.length + 1); return; }
    if (!inView) return;
    const wait = step > GROUP.length ? 5000 : step === 0 ? 500 : 1400;
    const timer = window.setTimeout(() => setStep((s) => (s > GROUP.length ? 0 : s + 1)), wait);
    return () => window.clearTimeout(timer);
  }, [step, inView, still]);
  return (
    <div className="lw-group" ref={ref} aria-label="A sample group chat">
      <div className="lw-group-head"><span className="lw-stack"><Face size={20} /><Face tone="dark" size={20} /><Face tone="dark" size={20} /></span><b>Dinner plans</b><small>Group chat</small></div>
      <div className="lw-group-body">
        {GROUP.slice(0, Math.min(step, GROUP.length)).map((m, i) => (
          m.who === "you"
            ? <p key={i} className="lw-you">{m.text}</p>
            : <div key={i} className="lw-gmsg"><Face tone={m.who === "echo" ? "light" : "dark"} size={22} /><div><b>{NAMES[m.who]}</b><p>{m.text}</p></div></div>
        ))}
        {step > GROUP.length ? <span className="lw-done"><Icon name="check" size={13} /> Done · 3 agents · 41s</span> : null}
      </div>
    </div>
  );
}

function Team() {
  return (
    <section className="lw-team" id="team" aria-labelledby="team-title">
      <div className="shell">
        <SectionHead kicker="Working together" id="team-title" title="A team that hands work to each other.">
          Put agents in a group chat, or let Echo pass parts of a task to the right teammate. They check each other's work before they call it done.
        </SectionHead>
        <div className="lw-team-grid">
          <Rise><GroupChat /></Rise>
          <div className="lw-team-side">
            <Rise className="lw-card">
              <span className="lw-card-tag"><Icon name="branch" size={15} /> A2A · optional</span>
              <h3>Open to other agents.</h3>
              <p>EchoSpeak speaks A2A, the open agent-to-agent protocol. Turn it on and set a key, and outside agents can send it tasks. It's off until you do.</p>
            </Rise>
            <Rise className="lw-card" delay={0.08}>
              <span className="lw-card-tag"><Icon name="learn" size={15} /> Learning · <em className="lw-badge">Preview</em></span>
              <h3>Gets better from what worked.</h3>
              <ol className="lw-ladder"><li>Work is graded</li><li>A lesson is proposed</li><li>Kept only if it checks out</li><li>Read before similar tasks</li></ol>
              <p>Lessons can suggest an approach. They can never change permissions, approvals, tools, settings or code. We're still measuring how much it helps.</p>
            </Rise>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Safety ───────────────────────────────────────────────────────────

const RULES: { icon: IconName; title: string; body: string }[] = [
  { icon: "house", title: "Lives on your PC.", body: "Chats, memories and files stay on your computer. With a local model, nothing leaves it." },
  { icon: "shield", title: "Asks before risky steps.", body: "Deleting, sending, pushing code and risky commands wait for you, with the exact command shown." },
  { icon: "research", title: "Web pages can't give orders.", body: "What an agent reads online is treated as information, never as instructions." },
  { icon: "model", title: "Keys stay secret.", body: "API keys and other secrets are hidden from logs and tool output." },
  { icon: "learn", title: "Learning can't grant anything.", body: "It writes advice and statistics only, never permissions or settings." },
  { icon: "branch", title: "Outside agents need a key.", body: "A2A is off by default. When on, callers need your key." },
];

function Safety() {
  const [answer, setAnswer] = useState<"" | "allow" | "deny">("");
  return (
    <section className="lw-safety" id="safety" aria-labelledby="safety-title">
      <div className="shell">
        <SectionHead kicker="Boundaries" id="safety-title" title="You stay in charge.">
          Agents act on your computer, so the limits are built in, not bolted on.
        </SectionHead>
        <div className="lw-safety-grid">
          <ul className="lw-rules">
            {RULES.map((r, i) => (
              <Rise as="li" key={r.title} delay={i * 0.04}><span><Icon name={r.icon} size={18} /></span><div><b>{r.title}</b><p>{r.body}</p></div></Rise>
            ))}
          </ul>
          <Rise className="lw-gate">
            {answer ? (
              <div className="lw-gate-done" key={answer} role="status">
                <Face size={30} />
                <p>{answer === "allow" ? "Pushed. Your 3 commits are on GitHub." : "Okay, I won't push. Nothing left your PC."}</p>
                <button type="button" className="lw-btn" onClick={() => setAnswer("")}>Ask again</button>
              </div>
            ) : (
              <>
                <div className="lw-gate-head"><Face size={24} /><b>Glados wants to run a command</b></div>
                <code>git push origin main</code>
                <p>Sends 3 commits to GitHub. Try it; it's only a demo.</p>
                <div className="lw-gate-actions">
                  <button type="button" className="lw-btn" onClick={() => setAnswer("deny")}>Deny</button>
                  <button type="button" className="lw-btn is-primary" onClick={() => setAnswer("allow")}>Allow</button>
                </div>
              </>
            )}
          </Rise>
        </div>
        <Link className="text-link lw-more" to="/docs/privacy">How EchoSpeak stays safe <Icon name="arrow" size={15} /></Link>
      </div>
    </section>
  );
}

// ── Download ─────────────────────────────────────────────────────────

function Download() {
  const release = useLatestRelease();
  return (
    <section className="lw-download" id="download" aria-labelledby="download-title">
      <div className="shell">
        <Rise className="lw-download-card">
          <EchoFace size={104} aura mode="idle" />
          <h2 id="download-title">Put a team of agents on your PC.</h2>
          <p>Install, pick a brain, say hi. EchoSpeak updates itself after that.</p>
          <DownloadButton />
          <div className="lw-brains">
            <div><b>Free, on your PC</b><span>LM Studio or Ollama with Qwen or Gemma. Nothing leaves your computer.</span></div>
            <div><b>Or a cloud model</b><span>Bring your own OpenAI, Gemini, Claude or Grok key. Switch any time.</span></div>
          </div>
          <div className="lw-links">
            <Link className="text-link" to="/docs/getting-started">Setup guide <Icon name="arrow" size={15} /></Link>
            <a className="text-link" href={release.notesUrl} target="_blank" rel="noreferrer">What's new <Icon name="arrow" size={15} /></a>
            <Link className="text-link" to="/docs/how-it-works">How it works <Icon name="arrow" size={15} /></Link>
          </div>
          <small className="lw-req">Windows 10 or 11 · 64-bit · MIT licensed</small>
        </Rise>
      </div>
    </section>
  );
}

export function Home() {
  return (
    <MotionConfig reducedMotion="user">
      <div className="site site-home">
        <style>{echoFaceStyles}</style>
        <SiteHeader />
        <main>
          <Hero />
          <Answers />
          <WatchARun />
          <Loop />
          <Agents />
          <Team />
          <Safety />
          <Download />
        </main>
        <SiteFooter />
      </div>
    </MotionConfig>
  );
}
