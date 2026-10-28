/** Lean runtime stream contract (see apps/backend/agent/lean/loop.py). */

export type LeanAgentRef = {
  id: string;
  name: string;
  title?: string;
  initials?: string;
  /** Which provider answered. Chat (agent id "chat") shows the model, not an agent. */
  provider?: string;
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
      /** Live line while it runs ("Reading rtings.com (1 of 2)"), from tool_progress. */
      detail?: string;
      /** Past-tense label once finished ("Searched “budget mic”"). */
      doneLabel?: string;
      /** What came back, in a few words ("14 results · read 2 pages"). */
      summary?: string;
      /** Counts behind the summary (results, pages, products). */
      meta?: Record<string, number>;
      /** Live structured state from the tool (tool_state), e.g. research workers. */
      state?: WorkersState;
    }
  | {
      kind: "approval";
      step: number;
      id: string;
      toolCallId?: string;
      tool: string;
      summary: string;
      args?: Record<string, unknown>;
      reason: string;
      decision: "" | "allow" | "deny" | "timeout" | "cancelled" | "answered" | "held";
      /** False when "For this chat" cannot apply (outside content read this turn). */
      grantable?: boolean;
      /** "Always allow" can grant this class of action; scopeLabel names the class. */
      persistable?: boolean;
      scopeLabel?: string;
      /** ask_user: a question with choices instead of a permission request. */
      question?: string;
      options?: string[];
      allowOther?: boolean;
      answer?: string;
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

/** Research workers under a lead's spawn_workers call (agent/lean/swarm.py Worker.public). */
export type WorkerRow = {
  id: string;
  index: number;
  goal: string;
  status: "queued" | "running" | "done" | "partial" | "failed" | "skipped" | "cancelled";
  steps: number;
  tools: number;
  tokens: number;
  elapsed_s: number;
  confidence: string;
  answer: string;
  note: string;
  activity: string;
  sources: { url: string; title: string; opened: boolean }[];
  open_questions: string[];
};
export type WorkersState = { kind: "workers"; budget: number; used: number; stop_reason: string; workers: WorkerRow[] };

export type LeanLiveState = {
  requestId: string;
  executionId: string;
  routing: boolean;
  /** Autonomous mode: routine approvals don't interrupt this run. */
  autonomous?: boolean;
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
  /** "reply": chosen agents answer once. "discussion" (shown as "Work together"): plan, assign, do and verify until done. */
  mode?: "reply" | "discussion";
  max_messages?: number;
};

export type LeanApproval = {
  id: string;
  tool: string;
  summary: string;
  reason: string;
};

/** Learning from verified experience (backend agent/learning). */
export type LessonStatus = "pending_review" | "probation" | "established" | "retired" | "quarantined";

export type LearningLesson = {
  id: string;
  agent_id: string;
  agent_name?: string;
  title: string;
  text: string;
  status: LessonStatus;
  kind: "do" | "avoid";
  task_kind: string;
  trusted: boolean;
  origin: string;
  source_episodes: string[];
  level: number;
  uses: number;
  wins: number;
  losses: number;
  /** Checked results of matching tasks where the lesson was held back at random (its control). */
  held_wins?: number;
  held_losses?: number;
  last_used_at: number;
  created_at: number;
  updated_at: number;
  edited: boolean;
  note: string;
};

export type LearningEvent = {
  id: number;
  lesson_id: string;
  at: number;
  actor: string;
  action: string;
  reason: string;
  before: Partial<LearningLesson> | null;
  after: Partial<LearningLesson> | null;
};

export type LearningEpisode = {
  id: string;
  created_at: number;
  agent_id: string;
  agent_name: string;
  goal: string;
  outcome: "success" | "failure" | "stopped" | "answered" | "error";
  level: number;
  reasons: string[];
  trusted: boolean;
  feedback: number;
  feedback_note: string;
  summary: string;
  task_kind: string;
  taint: string[];
  tamper: { what: string; target: string; expected: boolean }[];
  tools: { name: string; label: string; target: string; ok: boolean; not_run: boolean }[];
  lessons_used: string[];
  /** Lessons that matched but were held back at random: this task is part of their control group. */
  lessons_withheld?: string[];
  verified_success: boolean;
  failed: boolean;
  execution_id?: string;
  session_id?: string;
  /** solo | lead | worker (research workers and team helpers). */
  role?: string;
  /** "swarm" for research workers. */
  source?: string;
};

export type LearningKindStats = { wins: number; losses: number; checked: number; answered: number; confirmed: number; decided: number; rate: number };

export type LearningProfile = {
  agent_id: string;
  name: string;
  title: string;
  episodes: number;
  kinds: Record<string, LearningKindStats>;
  strengths: string[];
  weaknesses: string[];
  false_success: number;
  paused: boolean;
  track_record: string;
  lessons_proven: number;
  lessons_unproven: number;
};

export type LearningStatus = {
  mode: "on" | "off" | "control";
  enabled: boolean;
  reflection_daily_cap: number;
  reflections_today: number;
  playbook_size: number;
  paused_agents: string[];
  episodes: number;
  lessons: Record<LessonStatus, number>;
  reflections_pending: number;
};

export type ReliabilityRow = { name: string; ok: number; failed: number; recent_ok: number; recent_failed: number; last_failure_at: number; updated_at: number };
