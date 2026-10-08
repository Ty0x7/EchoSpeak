import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { safeMarkdownComponents } from "../widgets/SafeImage";
import remarkGfm from "remark-gfm";
import { RichMarkdown } from "../widgets/RichMarkdown";
import { WidgetView } from "../widgets/WidgetView";
import { groupSteps, phaseOf, stepsSummary, type ToolSeg } from "./steps";
import type { LeanMessageData, LeanSegment } from "./types";

/**
 * Agents are drawn as small faces, like the logo: Echo is white with dark eyes,
 * every teammate is dark with white eyes. `initials` is kept for callers but
 * no longer drawn.
 */
export function AgentAvatar({ id, name, size = 26 }: { id?: string; name?: string; initials?: string; size?: number }) {
  const key = String(id || name || "");
  // Spread the blinks so a group of faces never blinks in unison.
  const blinkDelay = `${(Array.from(key).reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 47) / 10}s`;
  return (
    <span
      className="lm-avatar lm-face"
      data-tone={id === "echo" ? "light" : "dark"}
      style={{ width: size, height: size, ["--av" as string]: `${size}px`, ["--blink" as string]: blinkDelay }}
      aria-hidden
    >
      <i />
      <i />
    </span>
  );
}

function useTicker(active: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setTick((v) => v + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
}

const fmtSeconds = (ms: number) => {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className="lm-chev" data-open={open ? "true" : "false"} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

const ThoughtText = React.memo(function ThoughtText({ text }: { text: string }) {
  return (
    <div className="lm-thought-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={safeMarkdownComponents}>{text}</ReactMarkdown>
    </div>
  );
});

function ThinkingBlock({ seg, live }: { seg: Extract<LeanSegment, { kind: "thinking" }>; live: boolean }) {
  const running = live && !seg.endedAt;
  const [open, setOpen] = useState(running);
  const [userToggled, setUserToggled] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  useTicker(running);

  // Follow along while live; fold away when the thought ends unless the user opened it.
  useEffect(() => {
    if (userToggled) return;
    setOpen(running);
  }, [running, userToggled]);

  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el && running) el.scrollTop = el.scrollHeight;
  }, [seg.text, running]);

  const elapsed = (seg.endedAt || Date.now()) - seg.startedAt;
  const text = seg.text.trim();
  if (!text && !running) return null;
  return (
    <div className="lm-think" data-running={running ? "true" : "false"} data-open={open ? "true" : "false"}>
      <button
        type="button"
        className="lm-think-head"
        onClick={() => {
          setUserToggled(true);
          setOpen((v) => !v);
        }}
        aria-expanded={open}
      >
        <span className="lm-think-icon" aria-hidden>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z" />
          </svg>
        </span>
        <span className={running ? "lm-shimmer" : undefined}>
          {running ? "Thinking" : elapsed >= 1000 ? `Thought for ${fmtSeconds(elapsed)}` : "Thought"}
        </span>
        {running ? <span className="lm-think-time">{fmtSeconds(elapsed)}</span> : null}
        <Chevron open={open} />
      </button>
      {open ? (
        <div className="lm-think-body" ref={bodyRef}>
          {text ? <ThoughtText text={text} /> : "…"}
        </div>
      ) : null}
    </div>
  );
}

function StateMark({ status }: { status: "running" | "done" | "failed" }) {
  return (
    <span className="lm-tool-state" aria-hidden>
      {status === "running" ? (
        <span className="lm-spin" />
      ) : status === "done" ? (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
      ) : (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M7 7l10 10M17 7L7 17" /></svg>
      )}
    </span>
  );
}

function ToolRow({ seg }: { seg: ToolSeg }) {
  const [open, setOpen] = useState(false);
  useTicker(seg.status === "running");
  const elapsed = seg.status === "running" ? Date.now() - seg.startedAt : seg.durationMs || 0;
  const label = seg.status === "done" && seg.doneLabel ? seg.doneLabel : seg.label;
  const sub = seg.status === "running" ? seg.detail : seg.summary;
  return (
    <div className="lm-tool" data-status={seg.status}>
      <button type="button" className="lm-tool-head" onClick={() => setOpen((v) => !v)} aria-expanded={open} disabled={!seg.output}>
        <StateMark status={seg.status} />
        <span className="lm-tool-text">
          <span className="lm-tool-label">{label}</span>
          {sub ? <span className="lm-tool-sub" key={seg.status === "running" ? sub : "summary"}>{sub}</span> : null}
        </span>
        {elapsed > 400 ? <span className="lm-tool-time">{fmtSeconds(elapsed)}</span> : null}
        {seg.output ? <Chevron open={open} /> : null}
      </button>
      {open && seg.output ? <pre className="lm-tool-output">{seg.output}</pre> : null}
    </div>
  );
}

/**
 * Several steps in a row fold into one line ("Searched the web ×3 · Read 5 pages"),
 * with the step that is running right now shown live under it. Click to see every step.
 */
function StepGroup({ tools }: { tools: ToolSeg[] }) {
  const [open, setOpen] = useState(false);
  const running = tools.filter((t) => t.status === "running");
  useTicker(running.length > 0);
  const summary = stepsSummary(tools);
  const status = running.length ? "running" : tools.some((t) => t.status === "failed") && tools.every((t) => t.status === "failed") ? "failed" : "done";
  const started = Math.min(...tools.map((t) => t.startedAt || Date.now()));
  const total = running.length ? Date.now() - started : tools.reduce((sum, t) => sum + (t.durationMs || 0), 0);
  return (
    <div className="lm-steps" data-status={status}>
      <button type="button" className="lm-steps-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <StateMark status={status} />
        <span className="lm-steps-summary">{summary || `Working through ${tools.length} steps`}</span>
        <span className="lm-steps-count">{tools.length} steps{total > 400 ? ` · ${fmtSeconds(total)}` : ""}</span>
        <Chevron open={open} />
      </button>
      {open ? (
        <div className="lm-steps-list">{tools.map((tool) => <ToolRow key={tool.id} seg={tool} />)}</div>
      ) : running.length ? (
        <div className="lm-steps-list is-live">{running.map((tool) => <ToolRow key={tool.id} seg={tool} />)}</div>
      ) : null}
    </div>
  );
}

function ApprovalCard({
  seg,
  onDecide,
}: {
  seg: Extract<LeanSegment, { kind: "approval" }>;
  onDecide?: (id: string, decision: "allow" | "deny" | "always") => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const decided = Boolean(seg.decision);
  const decide = async (decision: "allow" | "deny" | "always") => {
    if (!onDecide || busy) return;
    setBusy(true);
    try {
      await onDecide(seg.id, decision);
    } finally {
      setBusy(false);
    }
  };
  const verdict =
    seg.decision === "allow" ? "Allowed" : seg.decision === "deny" ? "Denied" : seg.decision === "timeout" ? "Timed out" : seg.decision === "cancelled" ? "Stopped" : "";
  if (decided) {
    return (
      <div className="lm-approval-done" data-decision={seg.decision}>
        <span>{verdict} by you</span>
        <span className="lm-approval-done-what">{seg.summary}</span>
      </div>
    );
  }
  return (
    <div className="lm-approval" data-decided={decided ? "true" : "false"} data-decision={seg.decision || "pending"}>
      <div className="lm-approval-text">
        <span className="lm-approval-kicker">{decided ? verdict : "Permission request"}<span className="lm-approval-tool">{seg.tool.replace(/_/g, " ")}</span></span>
        <strong>{seg.summary}</strong>
        {seg.reason && !decided ? <span className="lm-approval-reason">Asking because {seg.reason}.</span> : null}
        {seg.args && Object.keys(seg.args).length ? (
          <pre className="lm-approval-args" aria-label="Exactly what will run">{JSON.stringify(seg.args, null, 2)}</pre>
        ) : null}
      </div>
      {!decided ? (
        <div className="lm-approval-actions">
          <button type="button" className="es-btn es-btn-primary" disabled={busy} onClick={() => void decide("allow")}>{busy ? "Applying…" : "Allow once"}</button>
          <button type="button" className="es-btn" disabled={busy} onClick={() => void decide("always")} title={`Allow ${seg.tool} for the rest of this session`}>For this chat</button>
          <button type="button" className="es-btn es-btn-quiet" disabled={busy} onClick={() => void decide("deny")}>Decline</button>
        </div>
      ) : null}
    </div>
  );
}

/** Sources from every search/fetch in the message, merged and de-duplicated, shown once at the end. */
function mergedCitations(segments: LeanSegment[]): unknown | null {
  const seen = new Set<string>();
  const items: unknown[] = [];
  for (const seg of segments) {
    if (seg.kind !== "tool" || !seg.widgets) continue;
    for (const widget of seg.widgets as { type?: string; data?: { items?: { url?: string }[] } }[]) {
      if (widget?.type !== "citations") continue;
      for (const item of widget.data?.items || []) {
        const url = String(item?.url || "");
        if (url && !seen.has(url)) {
          seen.add(url);
          items.push(item);
        }
      }
    }
  }
  return items.length ? { type: "citations", data: { items: items.slice(0, 12) } } : null;
}

export function LeanMessage({
  data,
  live = false,
  showHeader = true,
  onDecide,
  onContinue,
  at,
}: {
  data: LeanMessageData;
  live?: boolean;
  showHeader?: boolean;
  onDecide?: (id: string, decision: "allow" | "deny" | "always") => Promise<void> | void;
  /** Offered when the agent stopped at the step limit. */
  onContinue?: () => void;
  at?: number;
}) {
  const streaming = live && data.status === "streaming";
  const segments = data.segments;
  const lastText = [...segments].reverse().find((s) => s.kind === "text");
  const phase = live ? phaseOf(data) : "";
  const items = React.useMemo(() => groupSteps(segments), [segments]);
  const sources = React.useMemo(() => mergedCitations(segments), [segments]);
  return (
    <article className="lm" data-status={data.status} data-live={streaming ? "true" : "false"} data-role={data.role || undefined}>
      {showHeader ? (
        <header className="lm-head">
          <AgentAvatar id={data.agent.id} name={data.agent.name} initials={data.agent.initials} />
          <span className="lm-name">{data.agent.name || "Echo"}</span>
          {data.agent.title ? <span className="lm-title">{data.agent.title}</span> : null}
          {data.role === "merge" ? <span className="lm-tag">Summary</span> : null}
          {data.delegatedBy ? <span className="lm-via" title={`${data.delegatedBy} handed this to ${data.agent.name}`}>via {data.delegatedBy}</span> : null}
          {at ? <time className="lm-time">{new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time> : null}
        </header>
      ) : null}
      <div className="lm-body">
        {items.map((item) => {
          if (item.kind === "steps") {
            const cards = item.tools.flatMap((tool) => (tool.widgets || []).filter((w) => (w as { type?: string })?.type !== "citations"));
            return (
              <React.Fragment key={item.key}>
                {item.tools.length > 1 ? <StepGroup tools={item.tools} /> : <ToolRow seg={item.tools[0]} />}
                {cards.map((widget, k) => <WidgetView key={k} widget={widget} />)}
              </React.Fragment>
            );
          }
          const { seg, index } = item;
          if (seg.kind === "thinking") return <ThinkingBlock key={`t${index}`} seg={seg} live={streaming} />;
          if (seg.kind === "approval") return <ApprovalCard key={`a${seg.id}`} seg={seg} onDecide={onDecide} />;
          if (seg.kind === "note") return <div key={`n${index}`} className="lm-note">{seg.text}</div>;
          if (seg.kind !== "text" || !seg.text.trim()) return null;
          const isTail = seg === lastText && streaming;
          return (
            <div key={`m${index}`} className="lm-text-wrap" data-tail={isTail ? "true" : "false"}>
              <RichMarkdown text={seg.text} streaming={isTail} />
            </div>
          );
        })}
        {phase ? (
          <div className="lm-phase" role="status"><span className="lm-dots" aria-hidden><i /><i /><i /></span><span key={phase}>{phase}</span></div>
        ) : null}
        {!streaming && sources ? <WidgetView widget={sources} /> : null}
        {data.outcome && !streaming ? (
          <div className="lm-outcome" data-status={data.outcome.status} role="status">
            {data.outcome.status === "done" ? (
              <>
                <span className="lm-outcome-mark" aria-hidden>✓</span>
                <span><b>Done:</b> {data.outcome.summary || "Finished."}</span>
              </>
            ) : (
              <span><b>Stopped:</b> {data.outcome.reason || "it could not continue."}</span>
            )}
          </div>
        ) : null}
        {data.stopReason === "max_steps" && !streaming ? (
          <div className="lm-stopped">
            <span>Stopped at the step limit</span>
            {onContinue ? <button type="button" className="es-btn es-btn-sm" onClick={onContinue}>Continue</button> : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}
