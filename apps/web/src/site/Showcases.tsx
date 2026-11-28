import React, { lazy, Suspense, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import type { LeanMessageData, LeanSegment } from "../lean/types";
import "../lean/lean.css";
import { useTheme } from "../theme/theme";
import { Icon } from "./Chrome";
import desktopDark from "./assets/showcase-desktop.webp";
import desktopDarkClose from "./assets/showcase-desktop-close.webp";
import desktopLight from "./assets/showcase-desktop-light.webp";
import desktopLightClose from "./assets/showcase-desktop-light-close.webp";
import terminalDark from "./assets/showcase-terminal.webp";
import terminalLight from "./assets/showcase-terminal-light.webp";
import "./showcases.css";

const AgentMessage = lazy(async () => {
  const { LeanMessage } = await import("../lean/LeanMessage");
  return { default: LeanMessage };
});

const workspaceViews = [
  { id: "desktop", label: "Desktop", icon: "chat", width: 1500, height: 850,
    dark: desktopDark, light: desktopLight, close: { dark: desktopDarkClose, light: desktopLightClose },
    alt: "EchoSpeak desktop with the Lantern workspace attached. In Planning a focus session, Echo calculates that 18 of 25 minutes remain." },
  { id: "terminal", label: "Terminal", icon: "terminal", width: 1060, height: 640,
    dark: terminalDark, light: terminalLight, close: null,
    alt: "The EchoSpeak terminal resumes Planning a focus session in Lantern and shows the same reply of 18 minutes remaining." },
] as const;

export function WorkspaceShowcase() {
  const { theme } = useTheme();
  const [selected, setSelected] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const other = 1 - selected;

  function changeTab(event: KeyboardEvent<HTMLButtonElement>) {
    const next = { ArrowRight: other, ArrowLeft: other, Home: 0, End: 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    setSelected(next);
    tabs.current[next]?.focus();
  }

  return (
    <section id="watch" className="shell h-workspace" aria-label="Recorded desktop and terminal preview" tabIndex={-1}>
      <div className="h-workspace-bar">
        <div role="tablist" aria-label="Conversation view">
          {workspaceViews.map(({ id, label, icon }, index) => (
            <button key={id} id={`workspace-tab-${id}`} role="tab" type="button"
              ref={(node) => { tabs.current[index] = node; }} aria-controls={`workspace-view-${id}`}
              tabIndex={selected === index ? 0 : -1} aria-selected={selected === index}
              onClick={() => setSelected(index)} onKeyDown={changeTab}>
              <Icon name={icon} size={17} />{label}
            </button>
          ))}
        </div>
        <span className={selected === 1 ? "h-terminal-label" : undefined}>
          <span className="h-recorded-label">Recorded in EchoSpeak</span>
          {selected === 1 && <span className="h-terminal-hint">Swipe to read</span>}
        </span>
      </div>
      <div className="h-window-deck" data-view={workspaceViews[selected].id}>
        {workspaceViews.map(({ id, width, height, alt, close, ...view }, index) => (
          <div className="h-workspace-view" key={id} id={`workspace-view-${id}`}
            role="tabpanel" aria-labelledby={`workspace-tab-${id}`} hidden={selected !== index}>
            <div className="h-workspace-capture" tabIndex={id === "terminal" ? 0 : undefined}
              aria-label={id === "terminal" ? "Terminal screenshot. Scroll sideways on narrow screens." : undefined}>
              <picture>
                {close && <source media="(max-width: 680px)" srcSet={close[theme]} width={824} height={458} />}
                <img src={view[theme]} width={width} height={height} alt={alt} decoding="async"
                  loading={index === 0 ? "eager" : "lazy"} />
              </picture>
            </div>
          </div>
        ))}
        <div className="h-workspace-layer" aria-hidden="true">
          <img src={workspaceViews[other][theme]} width={workspaceViews[other].width}
            height={workspaceViews[other].height} alt="" decoding="async" loading="lazy" />
        </div>
      </div>
    </section>
  );
}

type Tool = Extract<LeanSegment, { kind: "tool" }>;

const read: Tool = {
  kind: "tool", step: 1, id: "read", name: "file_read", label: "Reading timer.py",
  status: "done", doneLabel: "Read timer.py", summary: "Found the remaining-time calculation",
  output: "def remaining(total, elapsed):\n    return total - elapsed", startedAt: 0,
};
const edit: Tool = {
  kind: "tool", step: 2, id: "edit", name: "file_edit", label: "Editing timer.py",
  status: "done", doneLabel: "Edited timer.py", summary: "Clamp remaining minutes at zero",
  output: "-    return total - elapsed\n+    return max(0, total - elapsed)", startedAt: 0,
};
const test: Tool = {
  kind: "tool", step: 3, id: "test", name: "terminal", label: "Running the test suite",
  status: "done", doneLabel: "Ran python -m unittest", summary: "2 tests passed",
  output: "$ python -m unittest\nRan 2 tests\nOK", startedAt: 0,
};
const reply = (step: number, text: string): LeanSegment => ({ kind: "text", step, text });

const workflow = [
  {
    label: "Attach", context: "Lantern attached",
    segments: [reply(0, "I’ll check how the timer handles an overrun, then run the project’s tests.")],
  },
  {
    label: "Inspect", context: "Read the project file",
    segments: [read, reply(1, "`remaining()` subtracts elapsed time directly. That’s why an overrun shows negative minutes.")],
  },
  {
    label: "Run", context: "Edit and run the tests",
    segments: [edit, test],
  },
  {
    label: "Verify", context: "Tests pass after the edit",
    segments: [test, reply(3, "The timer now stops at **0 minutes**. The tests cover a normal countdown and an overrun."),
      { kind: "note", step: 3, text: "Verified · 2 tests passed after the edit" } as const],
  },
];

function MessageLoaded({ onLoad }: { onLoad: (loaded: boolean) => void }) {
  useEffect(() => onLoad(true), [onLoad]);
  return null;
}

export function AgentWalkthrough() {
  const [stage, setStage] = useState({ index: 0, startedAt: 0 });
  const [manual, setManual] = useState(false);
  const [showMessage, setShowMessage] = useState(false);
  const [messageLoaded, setMessageLoaded] = useState(false);
  const [inView, setInView] = useState(false);
  const [allowMotion, setAllowMotion] = useState(false);
  const preview = useRef<HTMLDivElement>(null);
  const step = workflow[stage.index];
  const playing = messageLoaded && inView && allowMotion && !manual && stage.index < workflow.length - 1;

  useEffect(() => {
    const panel = preview.current;
    if (!panel) return;
    const preloader = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      setShowMessage(true);
      preloader.disconnect();
    }, { rootMargin: "220px" });
    const visibility = new IntersectionObserver(([entry]) => {
      setInView(entry.isIntersecting && entry.intersectionRatio >= 0.35);
    }, { threshold: 0.35, rootMargin: "-55px 0px 0px" });
    preloader.observe(panel);
    visibility.observe(panel);
    return () => {
      preloader.disconnect();
      visibility.disconnect();
    };
  }, []);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const refresh = () => setAllowMotion(!reducedMotion.matches && !document.hidden);
    refresh();
    reducedMotion.addEventListener("change", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      reducedMotion.removeEventListener("change", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setTimeout(() => {
      setStage({ index: stage.index + 1, startedAt: Date.now() });
    }, 1550);
    return () => window.clearTimeout(timer);
  }, [playing, stage]);

  const running = playing && step.label === "Run";
  const data: LeanMessageData = {
    messageId: "site-agent-example", agent: { id: "glados", name: "Glados", title: "Builder" },
    status: running ? "streaming" : "done",
    segments: running ? [edit, { ...test, status: "running", output: "", detail: "python -m unittest", startedAt: stage.startedAt }] : step.segments,
    text: "", startedAt: stage.startedAt,
  };

  return (
    <section className="shell h-agents" aria-labelledby="agents-title">
      <div className="h-agents-copy">
        <h2 id="agents-title">Give an agent a job to finish.</h2>
        <p>Follow an agent as it reads your project, makes a change and checks the result.</p>
      </div>
      <figure className="h-agent-workflow">
        <figcaption>Illustrative workflow</figcaption>
        <div className="h-agent-window" id="agent-example" ref={preview}>
          <div className="h-agent-window-bar">
            <span><Icon name="file" size={15} /> Lantern</span>
            <span><Icon name="shield" size={15} /> Ask for risky actions</span>
          </div>
          <div className="h-agent-window-body" onFocus={() => setManual(true)}>
            <p className="h-agent-request">Fix the focus timer so it never shows negative minutes. Run the tests.</p>
            <div className="h-agent-response" key={stage.index}>
              {showMessage && <Suspense fallback={null}>
                <AgentMessage data={data} live={running} />
                <MessageLoaded onLoad={setMessageLoaded} />
              </Suspense>}
            </div>
          </div>
        </div>
        <div className="h-agent-track">
          <div className="h-agent-steps" role="group" aria-label="Walkthrough steps" onFocus={() => setManual(true)}>
            {workflow.map(({ label, context }, index) => (
              <button className="h-step" key={label} type="button" aria-pressed={stage.index === index}
                aria-label={`${label} · ${context}`} aria-controls="agent-example"
                onClick={() => { setShowMessage(true); setManual(true); setStage({ index, startedAt: Date.now() }); }}>
                {label}
              </button>
            ))}
          </div>
          <p className="h-stage-summary" aria-live={manual ? "polite" : "off"}>{step.context}</p>
        </div>
      </figure>
      <Link className="text-link h-agents-link" to="/docs/agents">Meet the agents</Link>
    </section>
  );
}
