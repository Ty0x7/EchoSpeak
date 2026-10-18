import React, { useEffect, useState } from "react";

/** One span of a reply's trace, as GET /lean/traces/{message_id} returns it (agent/lean/tracing.py). */
export type TraceSpan = {
  name: string;
  id: string;
  parent: string;
  start_ms: number;
  duration_ms: number;
  status: string;
  attrs: Record<string, string | number | boolean>;
};
export type TraceRecord = { trace_id: string; message_id: string; agent: string; spans: TraceSpan[] };

export type TraceKind = "model" | "tool" | "wait" | "other";
export type TraceRow = { id: string; label: string; detail: string; kind: TraceKind; failed: boolean; offset: number; width: number; ms: number };
export type TraceSummary = { totalMs: number; share: Record<TraceKind, number>; rows: TraceRow[] };

const KIND: Record<string, TraceKind> = { "model.call": "model", "tool.run": "tool", "approval.wait": "wait", reader: "model", "context.compact": "model" };

function describe(span: TraceSpan): { label: string; detail: string } {
  const a = span.attrs;
  switch (span.name) {
    case "model.call":
      return { label: `Thinking · step ${a.step ?? "?"}`, detail: [a.model, a.tool_calls ? `${a.tool_calls} tool call${a.tool_calls === 1 ? "" : "s"}` : "", a.prompt_tokens ? `${a.prompt_tokens} in / ${a.completion_tokens ?? 0} out` : ""].filter(Boolean).join(" · ") };
    case "tool.run":
      return { label: String(a.tool || "Tool"), detail: a.ok === false ? "failed" : "" };
    case "approval.wait":
      return { label: "Waiting for you", detail: `${a.tool ?? ""} · ${a.decision ?? ""}` };
    case "reader":
      return { label: "Reader", detail: a.instructions_found ? `${a.tool}: instructions left out` : String(a.tool ?? "") };
    case "context.compact":
      return { label: "Summarizing to fit", detail: "" };
    case "policy.decision":
      return { label: "Safety check", detail: `${a.tool ?? ""} · ${a.action ?? ""}` };
    default:
      return { label: span.name, detail: "" };
  }
}

/** Rows positioned on one time scale (the whole reply = 100%), plus where the time went. */
export function traceRows(record: TraceRecord): TraceSummary {
  const root = record.spans.find((s) => !s.parent) || record.spans[0];
  const totalMs = Math.max(1, root?.duration_ms || 0);
  const share: Record<TraceKind, number> = { model: 0, tool: 0, wait: 0, other: 0 };
  const rows = record.spans
    .filter((s) => s !== root)
    .sort((a, b) => a.start_ms - b.start_ms)
    .map((span) => {
      const kind = KIND[span.name] || "other";
      share[kind] += span.duration_ms;
      const offset = Math.min(100, Math.max(0, (span.start_ms / totalMs) * 100));
      return { id: span.id, ...describe(span), kind, failed: span.status === "error", offset,
               width: Math.min(100 - offset, Math.max(0.6, (span.duration_ms / totalMs) * 100)), ms: span.duration_ms };
    });
  // Parallel tools overlap, so shares are of summed time and can pass 100% together; cap each one.
  (Object.keys(share) as TraceKind[]).forEach((k) => { share[k] = Math.min(100, Math.round((share[k] / totalMs) * 100)); });
  return { totalMs, share, rows };
}

const fmt = (ms: number) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`);

export function TraceView({ apiBase, sessionId, messageId }: { apiBase: string; sessionId: string; messageId: string }) {
  const [state, setState] = useState<{ id: string; record?: TraceRecord; error?: string }>({ id: "" });
  useEffect(() => {
    if (!messageId) return;
    const controller = new AbortController();
    fetch(`${apiBase}/lean/traces/${encodeURIComponent(messageId)}?session_id=${encodeURIComponent(sessionId)}`, { signal: controller.signal, cache: "no-store" })
      .then(async (r) => (r.ok ? setState({ id: messageId, record: await r.json() }) : setState({ id: messageId, error: r.status === 404 ? "No timing for this reply yet. Replies from before 11.8 and replies older than seven days have none." : "Couldn't load the timing." })))
      .catch(() => { if (!controller.signal.aborted) setState({ id: messageId, error: "Couldn't load the timing." }); });
    return () => controller.abort();
  }, [apiBase, sessionId, messageId]);

  if (!messageId) return <div className="ap-empty">After Echo replies, this shows where the time went: thinking, tools, and waiting for you.</div>;
  if (state.id !== messageId || (!state.record && !state.error)) return <div className="ap-empty">Loading timing…</div>;
  if (state.error || !state.record) return <div className="ap-empty">{state.error}</div>;
  const { totalMs, share, rows } = traceRows(state.record);
  return (
    <div className="rp-trace">
      <p className="rp-trace-sum">
        <strong>{fmt(totalMs)}</strong> for the last reply · thinking {share.model}% · tools {share.tool}%{share.wait ? ` · waiting for you ${share.wait}%` : ""}
      </p>
      <ol>
        {rows.map((row) => (
          <li key={row.id} className="rp-trace-row" data-kind={row.kind} data-failed={row.failed || undefined}>
            <span className="rp-trace-name" title={row.detail || row.label}>{row.label}{row.detail ? <small>{row.detail}</small> : null}</span>
            <span className="rp-trace-track" aria-hidden><i style={{ left: `${row.offset}%`, width: `${row.width}%` }} /></span>
            <span className="rp-trace-ms">{fmt(row.ms)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
