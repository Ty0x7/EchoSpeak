/** Lean runtime stream contract (see apps/backend/agent/lean/loop.py). */

export type LeanAgentRef = {
  id: string;
  name: string;
  title?: string;
  initials?: string;
};

export type LeanSegment =
  | { kind: "thinking"; step: number; text: string; startedAt: number; endedAt?: number }
  | { kind: "text"; step: number; text: string }
  /** A marker in the timeline, e.g. "Earlier steps summarized to fit the context window". */
  | { kind: "note"; step: number; text: string }
  | {
      kind: "tool";
      step: number;
      id: string;
      name: string;
      label: string;
      status: "running" | "done" | "failed";
      output: string;
      durationMs?: number;
      startedAt: number;
      /** Cards built from the tool's own data (weather, products, sources...). Validated before rendering. */
      widgets?: unknown[];
    }
  | {
      kind: "approval";
      step: number;
      id: string;
      toolCallId?: string;
      tool: string;
      summary: string;
      reason: string;
      decision: "" | "allow" | "deny" | "timeout" | "cancelled";
    };

export type LeanMessageData = {
  messageId: string;
  agent: LeanAgentRef;
  segments: LeanSegment[];
  status: "streaming" | "done" | "failed";
  text: string;
  startedAt: number;
  endedAt?: number;
  /** Name of the agent that handed this work over. */
  delegatedBy?: string;
  /** "merge": the lead's summary after several agents answered at once. */
  role?: string;
  /** "max_steps" when the agent stopped at the step limit before finishing. */
  stopReason?: string;
  /** How a group chat or handed-off job ended; shown under its last message. */
  outcome?: LeanOutcome;
};

export type LeanOutcome = { status: "done" | "stopped"; summary?: string; reason?: string };

export type LeanLiveState = {
  requestId: string;
  executionId: string;
  routing: boolean;
  startedAt: number;
  order: string[];
  messages: Record<string, LeanMessageData>;
};

export type LeanEvent = {
  type: string;
  request_id?: string;
  message_id?: string;
  agent_id?: string;
  at?: number;
  [key: string]: any;
};

export type LeanPersona = {
  id: string;
  name: string;
  title: string;
  description: string;
  soul: string;
  avatar: string;
  initials: string;
  model: { provider: string; model_id: string };
  toolsets: string[];
  builtin: boolean;
};

export type LeanRoom = {
  id: string;
  name: string;
  kind: "group" | "direct";
  agent_ids: string[];
  thread_id: string;
  last_message_at: number;
  last_preview: string;
  updated_at: number;
  /** "reply": chosen agents answer once. "discussion": they take turns, then the lead concludes. */
  mode?: "reply" | "discussion";
  max_messages?: number;
};

export type LeanApproval = {
  id: string;
  tool: string;
  summary: string;
  reason: string;
};
