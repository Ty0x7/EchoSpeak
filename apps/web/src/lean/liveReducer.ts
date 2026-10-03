import type { LeanEvent, LeanLiveState, LeanMessageData, LeanSegment } from "./types";

/** Event types produced by the lean runtime. Anything else is legacy. */
export const LEAN_EVENT_TYPES = new Set([
  "run_start",
  "routing",
  "agent_start",
  "step_start",
  "reasoning_delta",
  "agent_token",
  "text_replace",
  "tool_start",
  "tool_end",
  "approval_request",
  "approval_resolved",
  "agent_done",
  "delegation",
  "token_usage",
  "memory_saved",
  "context_compacted",
  "run_outcome",
  "job_continue",
  "task_board",
]);

export function isLeanEvent(evt: LeanEvent): boolean {
  if (evt.type === "final") return evt.runtime === "lean";
  if (["run_start", "routing", "delegation", "run_outcome", "job_continue", "task_board"].includes(evt.type)) return true;
  return Boolean(evt.message_id) && LEAN_EVENT_TYPES.has(evt.type);
}

export const emptyLive = (requestId = ""): LeanLiveState => ({
  requestId,
  startedAt: Date.now(),
  executionId: "",
  routing: false,
  order: [],
  messages: {},
});

const nowMs = (evt: LeanEvent) => (evt.at ? Math.round(evt.at * 1000) : Date.now());

function closeThinking(segments: LeanSegment[], at: number): LeanSegment[] {
  return segments.map((seg) =>
    seg.kind === "thinking" && !seg.endedAt ? { ...seg, endedAt: at } : seg
  );
}

function appendText(segments: LeanSegment[], kind: "thinking" | "text", step: number, text: string, at: number): LeanSegment[] {
  const last = segments[segments.length - 1];
  if (last && last.kind === kind && last.step === step) {
    const next = [...segments];
    next[next.length - 1] = { ...last, text: last.text + text } as LeanSegment;
    return next;
  }
  const base = kind === "text" ? closeThinking(segments, at) : segments;
  const seg: LeanSegment =
    kind === "thinking" ? { kind, step, text, startedAt: at } : { kind, step, text };
  return [...base, seg];
}

function patchMessage(state: LeanLiveState, id: string, fn: (msg: LeanMessageData) => LeanMessageData): LeanLiveState {
  const msg = state.messages[id];
  if (!msg) return state;
  return { ...state, messages: { ...state.messages, [id]: fn(msg) } };
}

export function leanReducer(state: LeanLiveState, evt: LeanEvent): LeanLiveState {
  const at = nowMs(evt);
  const id = String(evt.message_id || "");
  switch (evt.type) {
    case "run_start":
      return { ...state, executionId: String(evt.execution_id || "") };
    case "routing":
      return { ...state, routing: true };
    case "run_outcome": {
      // The job's ending belongs under the last message of the run.
      const last = state.order[state.order.length - 1];
      if (!last) return state;
      const outcome = {
        status: evt.status === "stopped" ? ("stopped" as const) : ("done" as const),
        summary: evt.summary ? String(evt.summary) : undefined,
        reason: evt.reason ? String(evt.reason) : undefined,
      };
      return patchMessage(state, last, (m) => ({ ...m, outcome }));
    }
    case "job_continue": {
      const last = state.order[state.order.length - 1];
      if (!last) return state;
      const note = `Not done yet: ${String(evt.reason || "work remains")}. ${String(evt.agent || "An agent")} continues.`;
      return patchMessage(state, last, (m) => ({ ...m, segments: [...m.segments, { kind: "note", step: 0, text: note, at }] }));
    }
    case "task_board": {
      // The lead's plan, as owned tasks, under the message that made it.
      const last = state.order[state.order.length - 1];
      const tasks = Array.isArray(evt.tasks) ? evt.tasks : [];
      if (!last || !tasks.length) return state;
      const note = "Plan: " + tasks.map((t: any) => `${String(t.owner || "?")} → ${String(t.task || "")}`).join(" · ");
      return patchMessage(state, last, (m) => ({ ...m, segments: [...m.segments, { kind: "note", step: 0, text: note, at }] }));
    }
    case "agent_start": {
      const agent = evt.agent || { id: evt.agent_id, name: evt.agent_id };
      const msg: LeanMessageData = {
        messageId: id,
        agent: { id: String(agent.id || ""), name: String(agent.name || ""), title: agent.title, initials: agent.initials },
        segments: [],
        status: "streaming",
        text: "",
        startedAt: at,
        delegatedBy: evt.delegated_by?.name ? String(evt.delegated_by.name) : undefined,
        role: evt.role ? String(evt.role) : undefined,
      };
      return { ...state, routing: false, order: [...state.order.filter((x) => x !== id), id], messages: { ...state.messages, [id]: msg } };
    }
    case "reasoning_delta":
      return patchMessage(state, id, (m) => ({ ...m, segments: appendText(m.segments, "thinking", Number(evt.step || 0), String(evt.text || ""), at) }));
    case "agent_token":
      return patchMessage(state, id, (m) => ({ ...m, segments: appendText(m.segments, "text", Number(evt.step || 0), String(evt.data || ""), at) }));
    case "text_replace":
      return patchMessage(state, id, (m) => ({
        ...m,
        segments: m.segments.map((s) => (s.kind === "text" && s.step === Number(evt.step) ? { ...s, text: String(evt.text || "") } : s)),
      }));
    case "tool_start":
      return patchMessage(state, id, (m) => ({
        ...m,
        segments: [
          ...closeThinking(m.segments, at),
          {
            kind: "tool",
            step: Number(evt.step || 0),
            id: String(evt.id || ""),
            name: String(evt.name || "tool"),
            label: String(evt.label || evt.name || "tool"),
            status: "running",
            output: "",
            startedAt: at,
          },
        ],
      }));
    case "tool_end":
      return patchMessage(state, id, (m) => ({
        ...m,
        segments: m.segments.map((s) =>
          s.kind === "tool" && s.id === evt.id
            ? {
                ...s,
                status: evt.ok ? "done" : "failed",
                output: String(evt.output || ""),
                durationMs: Number(evt.duration_ms || 0),
                widgets: Array.isArray(evt.widgets) && evt.widgets.length ? evt.widgets : undefined,
              }
            : s
        ),
      }));
    case "approval_request":
      return patchMessage(state, id, (m) => ({
        ...m,
        segments: [
          ...closeThinking(m.segments, at),
          {
            kind: "approval",
            step: Number(evt.step || 0),
            id: String(evt.id || ""),
            toolCallId: String(evt.tool_call_id || ""),
            tool: String(evt.tool || ""),
            summary: String(evt.summary || ""),
            reason: String(evt.reason || ""),
            decision: "",
          },
        ],
      }));
    case "context_compacted":
      return patchMessage(state, id, (m) => ({
        ...m,
        segments: [...m.segments, { kind: "note", step: 0, text: "Earlier steps summarized to fit the context window" }],
      }));
    case "approval_resolved":
      return patchMessage(state, id, (m) => ({
        ...m,
        segments: m.segments.map((s) => (s.kind === "approval" && s.id === evt.id ? { ...s, decision: evt.decision } : s)),
      }));
    case "agent_done":
      // A continuation after a handoff that had nothing to add: drop it.
      if (evt.empty && state.messages[id]) {
        const { [id]: _dropped, ...rest } = state.messages;
        return { ...state, order: state.order.filter((x) => x !== id), messages: rest };
      }
      return patchMessage(state, id, (m) => ({
        ...m,
        status: evt.success === false && evt.error ? "failed" : "done",
        text: String(evt.text || ""),
        stopReason: evt.stop_reason ? String(evt.stop_reason) : undefined,
        endedAt: at,
        segments: closeThinking(m.segments, at).map((s) =>
          s.kind === "tool" && s.status === "running" ? { ...s, status: "failed" } : s
        ),
      }));
    default:
      return state;
  }
}

/** Rebuild a message from the timeline persisted with the assistant item. */
export function messageFromTimeline(args: {
  messageId: string;
  agentId: string;
  agentName: string;
  initials?: string;
  title?: string;
  text: string;
  timeline: any[];
  at: number;
  success?: boolean;
  delegatedBy?: string;
  role?: string;
  stopReason?: string;
}): LeanMessageData {
  const segments: LeanSegment[] = [];
  for (const row of args.timeline || []) {
    const step = Number(row?.step || 0);
    const startedAt = Math.round(Number(row?.at || 0) * 1000) || args.at;
    if (row?.kind === "thinking") {
      const endedAt = Math.round(Number(row.ended_at || 0) * 1000) || startedAt;
      segments.push({ kind: "thinking", step, text: String(row.text || ""), startedAt, endedAt });
    }
    else if (row?.kind === "text") segments.push({ kind: "text", step, text: String(row.text || "") });
    else if (row?.kind === "note") segments.push({ kind: "note", step, text: String(row.text || "") });
    else if (row?.kind === "tool")
      segments.push({
        kind: "tool",
        step,
        id: String(row.id || ""),
        name: String(row.name || "tool"),
        label: String(row.label || row.name || "tool"),
        status: row.status === "running" ? "failed" : row.status === "failed" ? "failed" : "done",
        output: String(row.output || ""),
        durationMs: Number(row.duration_ms || 0),
        startedAt,
        widgets: Array.isArray(row.widgets) && row.widgets.length ? row.widgets : undefined,
      });
    else if (row?.kind === "approval")
      segments.push({
        kind: "approval",
        step,
        id: String(row.id || ""),
        tool: String(row.tool || ""),
        summary: String(row.summary || ""),
        reason: String(row.reason || ""),
        decision: row.decision || "deny",
      });
  }
  // Thinking durations: end at the next segment's start when known.
  for (let i = 0; i < segments.length; i += 1) {
    const seg = segments[i];
    if (seg.kind !== "thinking") continue;
    const next = segments.slice(i + 1).find((s) => s.kind === "tool") as Extract<LeanSegment, { kind: "tool" }> | undefined;
    if (next && next.startedAt > seg.startedAt) seg.endedAt = next.startedAt;
  }
  if (!segments.some((s) => s.kind === "text") && args.text) segments.push({ kind: "text", step: 0, text: args.text });
  return {
    messageId: args.messageId,
    agent: { id: args.agentId, name: args.agentName || "Echo", initials: args.initials, title: args.title },
    segments,
    status: args.success === false ? "failed" : "done",
    text: args.text,
    startedAt: args.at,
    endedAt: args.at,
    delegatedBy: args.delegatedBy || undefined,
    role: args.role || undefined,
    stopReason: args.stopReason || undefined,
  };
}
