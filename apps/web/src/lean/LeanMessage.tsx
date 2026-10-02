import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { LeanMessageData, LeanSegment } from "./types";

export function AgentAvatar({ id, name, initials, size = 26 }: { id?: string; name?: string; initials?: string; size?: number }) {
  const label = (initials || name || "?").slice(0, 2);
  return (
    <span className="lm-avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }} aria-hidden>
      {id === "echo" ? <img src="/logo.png" alt="" /> : label}
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
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
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

function ToolRow({ seg }: { seg: Extract<LeanSegment, { kind: "tool" }> }) {
  const [open, setOpen] = useState(false);
  useTicker(seg.status === "running");
  const elapsed = seg.status === "running" ? Date.now() - seg.startedAt : seg.durationMs || 0;
  return (
    <div className="lm-tool" data-status={seg.status}>
      <button type="button" className="lm-tool-head" onClick={() => setOpen((v) => !v)} aria-expanded={open} disabled={!seg.output}>
        <span className="lm-tool-state" aria-hidden>
          {seg.status === "running" ? (
            <span className="lm-spin" />
          ) : seg.status === "done" ? (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M7 7l10 10M17 7L7 17" /></svg>
          )}
        </span>
        <span className="lm-tool-label">{seg.label}</span>
        {elapsed > 400 ? <span className="lm-tool-time">{fmtSeconds(elapsed)}</span> : null}
        {seg.output ? <Chevron open={open} /> : null}
      </button>
      {open && seg.output ? <pre className="lm-tool-output">{seg.output}</pre> : null}
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
        <span className="lm-approval-kicker">{decided ? verdict : "Needs your OK"}</span>
        <strong>{seg.summary}</strong>
        {seg.reason && !decided ? <span className="lm-approval-reason">Asking because {seg.reason}.</span> : null}
      </div>
      {!decided ? (
        <div className="lm-approval-actions">
          <button type="button" className="es-btn es-btn-primary" disabled={busy} onClick={() => void decide("allow")}>Allow</button>
          <button type="button" className="es-btn" disabled={busy} onClick={() => void decide("always")} title={`Allow ${seg.tool} for the rest of this session`}>Always</button>
          <button type="button" className="es-btn es-btn-quiet" disabled={busy} onClick={() => void decide("deny")}>Deny</button>
        </div>
      ) : null}
    </div>
  );
}

const Markdown = React.memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="chat-markdown lm-text">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

export function LeanMessage({
  data,
  live = false,
  showHeader = true,
  onDecide,
  at,
}: {
  data: LeanMessageData;
  live?: boolean;
  showHeader?: boolean;
  onDecide?: (id: string, decision: "allow" | "deny" | "always") => Promise<void> | void;
  at?: number;
}) {
  const streaming = live && data.status === "streaming";
  const segments = data.segments;
  const lastText = [...segments].reverse().find((s) => s.kind === "text");
  const waiting = streaming && segments.length === 0;
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
        {waiting ? (
          <div className="lm-waiting" aria-hidden><span className="lm-dots"><i /><i /><i /></span></div>
        ) : null}
        {segments.map((seg, index) => {
          if (seg.kind === "thinking") return <ThinkingBlock key={`t${index}`} seg={seg} live={streaming} />;
          if (seg.kind === "tool") return <ToolRow key={`x${seg.id || index}`} seg={seg} />;
          if (seg.kind === "approval") return <ApprovalCard key={`a${seg.id}`} seg={seg} onDecide={onDecide} />;
          if (!seg.text.trim()) return null;
          const isTail = seg === lastText && streaming;
          return (
            <div key={`m${index}`} className="lm-text-wrap" data-tail={isTail ? "true" : "false"}>
              <Markdown text={seg.text} />
            </div>
          );
        })}
      </div>
    </article>
  );
}
