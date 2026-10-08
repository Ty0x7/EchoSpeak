import React, { useEffect, useReducer, useRef, useState } from "react";
import { Face, Icon, type IconName } from "./Chrome";
import { AGENTS, RUNS, stepsFor, type AgentId, type Decision, type RunStep, type ToolId } from "./runs";

/**
 * "Watch a run": the front page's flagship. A small player walks through one
 * run script at a time: the plan on the left, agents and tools on the canvas,
 * every step in the trace, and the inspector on the right. Approvals stop the
 * run until the visitor answers. It plays only while on screen, and with
 * reduced motion it starts finished and moves one step per click.
 */

const reducedMotion = () => typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

export type Player = { run: number; cursor: number; t: number; decision: Decision | null; playing: boolean };
type Action =
  | { type: "tick"; dt: number }
  | { type: "step" }
  | { type: "decide"; decision: Decision }
  | { type: "select"; run: number; play: boolean }
  | { type: "toggle" };

export function reducer(s: Player, a: Action): Player {
  const steps = stepsFor(RUNS[s.run], s.decision);
  const current = steps[s.cursor] as RunStep | undefined;
  const blocked = !current || (current.kind === "approval" && !s.decision);
  switch (a.type) {
    case "tick": {
      if (blocked || !s.playing) return s;
      const t = s.t + a.dt;
      return t >= current.ms ? { ...s, cursor: s.cursor + 1, t: 0 } : { ...s, t };
    }
    case "step":
      return blocked ? s : { ...s, cursor: s.cursor + 1, t: 0 };
    case "decide":
      return current?.kind === "approval" && !s.decision ? { ...s, decision: a.decision, cursor: s.cursor + 1, t: 0, playing: true } : s;
    case "select":
      return { run: a.run, cursor: 0, t: 0, decision: null, playing: a.play };
    case "toggle":
      return !current ? { run: s.run, cursor: 0, t: 0, decision: null, playing: true } : { ...s, playing: !s.playing };
  }
}

// Canvas layout, in a 500 × 300 box (percentages of it place the nodes).
const POS: Record<AgentId | ToolId, [number, number]> = {
  echo: [50, 15], jarvis: [23, 48], glados: [77, 48],
  web: [11, 85], memory: [31, 85], files: [51, 85], terminal: [70, 85], gate: [89, 85],
};
const EDGES: [AgentId, AgentId | ToolId][] = [
  ["echo", "jarvis"], ["echo", "glados"], ["echo", "memory"], ["jarvis", "web"],
  ["glados", "memory"], ["glados", "files"], ["glados", "terminal"], ["glados", "gate"],
];
const TOOLS: { id: ToolId; label: string; icon: IconName }[] = [
  { id: "web", label: "Web", icon: "research" },
  { id: "memory", label: "Memory", icon: "memory" },
  { id: "files", label: "Files", icon: "file" },
  { id: "terminal", label: "Terminal", icon: "terminal" },
  { id: "gate", label: "Approval", icon: "shield" },
];

const edgeOf = (step: RunStep | undefined): string => {
  if (!step) return "";
  if (step.kind === "handoff" && step.to) return `${step.agent}-${step.to}`;
  return step.tool ? `${step.agent}-${step.tool}` : "";
};

const curve = (from: [number, number], to: [number, number]) => {
  const [x1, y1, x2, y2] = [from[0] * 5, from[1] * 3, to[0] * 5, to[1] * 3];
  const mid = (y1 + y2) / 2;
  return `M${x1} ${y1} C${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
};

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)}s`;

type Status = "done" | "running" | "waiting" | "denied";

function Mark({ status }: { status: Status | "pending" }) {
  return (
    <span className="ws-mark" data-status={status} aria-hidden="true">
      {status === "running" ? <i className="ws-spin" /> : status === "done" ? <Icon name="check" size={12} /> : status === "waiting" ? <b>!</b> : status === "denied" ? <b>×</b> : null}
    </span>
  );
}

function Result({ runId, decision }: { runId: string; decision: Decision | null }) {
  if (runId === "research") {
    return (
      <div className="ws-result">
        <p>Here are my top three under $100:</p>
        <table>
          <thead><tr><th>Mic</th><th>Price</th><th>Best for</th></tr></thead>
          <tbody>
            <tr><td>HyperX SoloCast</td><td>$49</td><td>Plug and play</td></tr>
            <tr><td>Fifine AM8</td><td>$59</td><td>USB now, XLR later</td></tr>
            <tr><td>Samson Q2U</td><td>$69</td><td>Noisy rooms</td></tr>
          </tbody>
        </table>
        <div className="ws-sources">{["rtings.com", "soundguys.com", "youtube.com"].map((s, i) => <span key={s}><b>{i + 1}</b>{s}</span>)}</div>
      </div>
    );
  }
  if (runId === "code") {
    return (
      <div className="ws-result">
        <p>{decision === "deny" ? "Fixed and tested. Nothing was pushed; the change is ready when you are." : "Fixed, tested and pushed to main."}</p>
        <pre className="ws-diff">{"calc.py\n-    return a / b\n+    if b == 0:\n+        raise ValueError(\"can't divide by zero\")\n+    return a / b"}</pre>
        <div className="ws-chips"><span className="is-ok"><Icon name="check" size={12} /> 12 tests passing</span><span>{decision === "deny" ? "Not pushed" : "main · pushed"}</span></div>
      </div>
    );
  }
  return (
    <div className="ws-result">
      <p>Garlic lemon pasta tonight: vegetarian, about 20 minutes. Your list is in Notes.</p>
      <div className="ws-file"><Icon name="file" size={15} /><b>shopping-list.md</b><small>7 items</small></div>
      <div className="ws-chips"><span className="is-mem"><Icon name="memory" size={12} /> Used: vegetarian, under 30 minutes</span></div>
    </div>
  );
}

export function Workspace() {
  const still = reducedMotion();
  const [s, dispatch] = useReducer(reducer, undefined, () => {
    const first = stepsFor(RUNS[0], null);
    return still ? { run: 0, cursor: first.length, t: 0, decision: null, playing: false } : { run: 0, cursor: 0, t: 0, decision: null, playing: true };
  });
  const rootRef = useRef<HTMLDivElement | null>(null);
  const traceRef = useRef<HTMLOListElement | null>(null);
  const touched = useRef(still);
  const [inView, setInView] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);

  const run = RUNS[s.run];
  const steps = stepsFor(run, s.decision);
  const current = steps[s.cursor] as RunStep | undefined;
  const finished = !current;
  const waiting = current?.kind === "approval" && !s.decision;
  const statusOf = (i: number): Status | "pending" => {
    if (i < s.cursor) return steps[i].kind === "approval" && s.decision === "deny" ? "denied" : "done";
    if (i === s.cursor) return waiting ? "waiting" : "running";
    return "pending";
  };
  const liveLine = current?.live?.length ? current.live[Math.min(current.live.length - 1, Math.floor(s.t / (current.ms / current.live.length)))] : undefined;
  const elapsed = steps.slice(0, s.cursor).reduce((sum, step) => sum + step.ms, 0) + s.t;

  // Play only while the workspace is on screen.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof IntersectionObserver === "undefined") { setInView(true); return; }
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.2 });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!inView || !s.playing || finished || waiting) return;
    let last = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      dispatch({ type: "tick", dt: now - last });
      last = now;
    }, 80);
    return () => window.clearInterval(timer);
  }, [inView, s.playing, finished, waiting]);
  // Until the visitor takes over, finished runs move on to the next task.
  useEffect(() => {
    if (!finished || touched.current || !inView) return;
    const timer = window.setTimeout(() => dispatch({ type: "select", run: (s.run + 1) % RUNS.length, play: true }), 6500);
    return () => window.clearTimeout(timer);
  }, [finished, inView, s.run]);
  useEffect(() => { setPicked(null); }, [s.run]);
  useEffect(() => {
    const list = traceRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [s.cursor, s.run]);

  const take = (fn: () => void) => () => { touched.current = true; fn(); };
  const selectedIndex = (() => {
    if (picked) { const i = steps.findIndex((step) => step.id === picked); if (i >= 0 && i <= s.cursor) return i; }
    return finished ? steps.length - 1 : s.cursor;
  })();
  const selected = steps[selectedIndex];
  const selectedStatus = statusOf(selectedIndex);
  const activeEdge = finished || waiting ? (waiting ? edgeOf(current) : "") : edgeOf(current);
  const usedEdges = new Set(steps.slice(0, s.cursor).map(edgeOf).filter(Boolean));
  const usedTools = new Set(steps.slice(0, s.cursor).map((step) => step.tool).filter(Boolean) as ToolId[]);
  const agentState = (id: AgentId) => {
    if (current?.agent === id) return waiting ? "waiting" : "working";
    if (current?.kind === "handoff" && current.to === id) return "receiving";
    if (steps.slice(0, s.cursor).some((step) => step.agent === id || step.to === id)) return finished ? "done" : "standing by";
    return "idle";
  };
  const planState = (i: number) => {
    const mine = steps.filter((step) => step.plan === i);
    if (current?.plan === i) return waiting ? "waiting" : "running";
    if (mine.length && mine.every((step) => steps.indexOf(step) < s.cursor)) {
      return mine.some((step) => step.kind === "approval") && s.decision === "deny" ? "denied" : "done";
    }
    return "pending";
  };
  const titleOf = (step: RunStep, status: Status | "pending") =>
    status === "denied" && step.deniedDone ? step.deniedDone : status === "done" ? step.done : step.label;
  const statusText = waiting ? `${AGENTS[current!.agent].name} is waiting for you` : finished ? `Done in ${seconds(elapsed)}` : `${AGENTS[current!.agent].name}: ${current!.label}`;

  return (
    <div className="ws" ref={rootRef} data-waiting={waiting ? "true" : "false"}>
      <div className="ws-bar">
        <div className="ws-tabs" role="group" aria-label="Choose a task">
          {RUNS.map((r, i) => (
            <button key={r.id} type="button" aria-pressed={i === s.run} className={i === s.run ? "is-on" : ""} onClick={take(() => dispatch({ type: "select", run: i, play: !still }))}>{r.tab}</button>
          ))}
        </div>
        <p className="ws-status" aria-live="polite"><span data-state={waiting ? "waiting" : finished ? "done" : "running"} />{statusText}</p>
        <div className="ws-controls">
          <button type="button" onClick={take(() => dispatch({ type: "toggle" }))} aria-label={finished ? "Run again" : s.playing ? "Pause" : "Play"} title={finished ? "Run again" : s.playing ? "Pause" : "Play"}>
            {finished ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.5-5.8M4 4v4.5h4.5" /></svg>
              : s.playing ? <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="1.5" width="2.8" height="9" rx="1" fill="currentColor" /><rect x="7.2" y="1.5" width="2.8" height="9" rx="1" fill="currentColor" /></svg>
              : <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.8v8.4L10 6z" fill="currentColor" /></svg>}
          </button>
          <button type="button" onClick={take(() => dispatch({ type: "step" }))} disabled={finished || waiting} aria-label="Next step" title="Next step">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 5l8 7-8 7M18 5v14" /></svg>
          </button>
        </div>
      </div>

      <div className="ws-body">
        <div className="ws-run">
          <div className="ws-ask"><span>You</span><p>{run.ask}</p></div>
          <h4>Plan</h4>
          <ol className="ws-plan">
            {run.plan.map((item, i) => (
              <li key={item} data-state={planState(i)}><Mark status={planState(i) as Status | "pending"} />{item}</li>
            ))}
          </ol>
          <h4>Steps</h4>
          <ol className="ws-trace" ref={traceRef}>
            {steps.slice(0, Math.min(steps.length, s.cursor + 1)).map((step, i) => {
              const status = statusOf(i);
              const sub = status === "running" ? liveLine : status === "waiting" ? "Waiting for your answer" : step.summary;
              return (
                <li key={step.id}>
                  <button type="button" className={i === selectedIndex ? "is-picked" : ""} onClick={take(() => setPicked(step.id))} aria-pressed={i === selectedIndex}>
                    <Mark status={status} />
                    <span className="ws-step-text"><b>{titleOf(step, status)}</b>{sub ? <small key={sub}>{sub}</small> : null}</span>
                    {status === "done" && step.ms > 400 ? <time>{seconds(step.ms)}</time> : null}
                  </button>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="ws-canvas" aria-hidden="true">
          <svg viewBox="0 0 500 300" preserveAspectRatio="xMidYMid meet">
            {EDGES.map(([from, to]) => {
              const key = `${from}-${to}`;
              const state = key === activeEdge ? (waiting ? "waiting" : "active") : usedEdges.has(key) ? "used" : "idle";
              return <path key={key} d={curve(POS[from], POS[to])} className="ws-edge" data-state={state} />;
            })}
          </svg>
          {(Object.keys(AGENTS) as AgentId[]).map((id) => (
            <div key={id} className="ws-agent" data-state={agentState(id)} style={{ left: `${POS[id][0]}%`, top: `${POS[id][1]}%` }}>
              <Face tone={AGENTS[id].tone} size={id === "echo" ? 44 : 36} />
              <b>{AGENTS[id].name}</b>
              <small>{agentState(id)}</small>
            </div>
          ))}
          {TOOLS.map((tool) => (
            <div key={tool.id} className="ws-tool" data-tool={tool.id} data-state={current?.tool === tool.id ? (waiting ? "waiting" : "active") : usedTools.has(tool.id) ? "used" : "idle"} style={{ left: `${POS[tool.id][0]}%`, top: `${POS[tool.id][1]}%` }}>
              <Icon name={tool.icon} size={15} />{tool.label}
            </div>
          ))}
        </div>

        <aside className="ws-inspector" aria-label="Inspector">
          {selected ? (
            <div className="ws-inspect" key={`${run.id}-${selected.id}-${selectedStatus}`}>
              <span className="ws-eyebrow">Step {selectedIndex + 1} of {steps.length}{selectedStatus === "done" && selected.ms > 400 ? ` · ${seconds(selected.ms)}` : ""}</span>
              <h4><Mark status={selectedStatus} />{titleOf(selected, selectedStatus)}</h4>
              <div className="ws-who"><Face tone={AGENTS[selected.agent].tone} size={18} />{AGENTS[selected.agent].name}<span>{AGENTS[selected.agent].role}</span></div>
              {selectedStatus === "waiting" ? (
                <div className="ws-approval">
                  <p><b>{AGENTS[selected.agent].name} wants to run a command</b></p>
                  <code>git push origin main</code>
                  <p className="ws-approval-why">{selected.output}</p>
                  <div className="ws-approval-actions">
                    <button type="button" onClick={take(() => dispatch({ type: "decide", decision: "deny" }))}>Deny</button>
                    <button type="button" className="is-primary" onClick={take(() => dispatch({ type: "decide", decision: "allow" }))}>Allow</button>
                  </div>
                  <small>It's a demo. Nothing leaves this page.</small>
                </div>
              ) : selected.kind === "answer" && selectedStatus === "done" ? (
                <Result runId={run.id} decision={s.decision} />
              ) : (
                <>
                  {selectedStatus === "running" && liveLine ? <p className="ws-live">{liveLine}</p> : selected.summary && selectedStatus !== "running" ? <p className="ws-summary">{selected.summary}</p> : null}
                  {selected.output ? <pre>{selected.output}</pre> : null}
                </>
              )}
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
