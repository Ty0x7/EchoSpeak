// Moved out of index.tsx (10.0 split). Kept verbatim.
import { type TaskPlanProjection } from "../taskPlanProjection";
import { type ResearchRun } from "../features/research/types";
import { type ResponseRenderIntent, type ResponseRenderPlan } from "../features/responseRenderer/types";
import { type ChatEmbed } from "../features/embeds/types";
import { type OperationalApproval, type OperationalThreadState } from "../features/operations/OperationalStateCard";
import { type LeanMessageData } from "../lean/types";
import { type SemanticActivityEvent } from "../agentActivity";

export type Role = "user" | "assistant";
export type DocSource = {
  id: string;
  filename?: string;
  source?: string;
  chunk?: number;
};
export type RuntimeSettingsEnvelope = {
  settings: Record<string, any>;
  overrides: Record<string, any>;
  issues?: { key: string; message: string; severity: "error" | "warning" }[];
};
export type SettingsTestResult = {
  ok: boolean;
  target: string;
  message: string;
  latency_ms?: number;
};
export type StreamEvent =
  | { type: "partial"; text: string }
  | { type: "final"; text: string }
  | { type: "error"; message: string };

export type MessageUsage = {
  /** Estimated tokens in this bubble */
  tokens: number;
  /** Estimated session/context tokens used after this message */
  contextUsed: number;
  /** Configured context window at send time */
  contextWindow: number;
  provider?: string;
  model?: string;
};

export type Message = {
  id: string;
  role: Role;
  text: string;
  at: number;
  usage?: MessageUsage;
  /** True when the reply was already shown live via stream tokens — skip typewriter re-reveal */
  skipTypewriter?: boolean;
  /**
   * Multi-beat: "partial" = first spoken mid-turn line (tools must render below it).
   * "final" = post-tool answer. Used for timeline ordering only.
   */
  streamBeat?: "partial" | "final";
  /** Structured assistant response plan. Plain text remains the fallback. */
  renderPlan?: ResponseRenderPlan;
  /** Research / weather / source embeds under the final answer. */
  embeds?: ChatEmbed[];
  /** Authoritative backend state captured for this assistant turn. */
  operation?: { state: OperationalThreadState; success: boolean; executionId?: string };
  /** Retrieval sources captured for this exact assistant turn. */
  docSources?: DocSource[];
  /** Durable Turn / execution id from backend (maps client stream key → history). */
  executionId?: string;
  /** Client stream key used while the Turn was open (debugging correlation). */
  clientRequestId?: string;
  /** Lean runtime: the agent who spoke and the thinking/tool timeline behind the reply. */
  lean?: LeanMessageData;
};

/** Rough client-side token estimate (chars / 3.5) — matches context meter */
export type AgentStreamEvent = (
  | { type: "tool_start"; id: string; name: string; input: string; at: number; request_id?: string }
  | { type: "tool_end"; id: string; name?: string; output: string; research?: ResearchRun; outcome?: { success: boolean; status: string; error_code?: string; error_message?: string; retryable?: boolean }; at: number; request_id?: string }
  | { type: "tool_error"; id: string; error: string; at: number; request_id?: string }
  | { type: "thinking"; content: string; at: number; request_id?: string }
  | { type: "thinking_step"; step_type: string; content: string; status: string; at: number; request_id?: string }
  | { type: "agent_token"; data: string; at: number; request_id?: string }
  | { type: "memory_saved"; memory_count: number; at: number; request_id?: string }
  | { type: "task_plan"; data: any[]; at?: number; request_id?: string }
  | { type: "partial_reply"; response: string; speak?: boolean; segment?: number; reason?: string; request_id?: string; at: number }
  | { type: "final"; response: string; spoken_text?: string; success: boolean; memory_count: number; doc_sources?: DocSource[]; research?: ResearchRun[]; response_render?: ResponseRenderIntent; execution_id?: string; trace_id?: string; thread_state?: ThreadSessionState | null; execution_projection?: Record<string, any>; partial_replies?: string[]; voice_turn_id?: string; request_id?: string; at: number }
  | { type: "turn_bound"; request_id?: string; execution_id?: string; turn_id?: string; thread_id?: string; active_project_id?: string; model?: string; reasoning_control?: Record<string, unknown>; at: number }
  | { type: "iteration_boundary"; iteration: number; phase?: string; model?: string; request_id?: string; at: number }
  | { type: "token_usage"; prompt?: number; completion?: number; total?: number; reasoning?: number; approximate?: boolean; request_id?: string; at: number }
  | { type: "reasoning_summary"; content: string; iteration?: number; request_id?: string; at: number }
  | { type: "provider_retry"; attempt?: number; retrying?: boolean; reason_code?: string; request_id?: string; at: number }
  | { type: "recovery"; message: string; request_id?: string; at: number }
  | { type: "lifecycle"; phase: string; execution_id?: string; error?: string; request_id?: string; at?: number }
  | { type: "error"; message: string; at: number; request_id?: string }
) & { activity?: SemanticActivityEvent; seq?: number };

/** Square spinner — Echo's shape (startup-style), no generic browser spinner / emoji */
export type GatewayEvent =
  | { type: "gateway_ready"; session_id?: string; at?: number }
  | { type: "discord_activity"; tool?: string; source?: string; at?: number }
  | { type: "spotify_playback"; is_playing?: boolean; track_id?: string; track_name?: string; track_artist?: string; duration_ms?: number; progress_ms?: number; at?: number }
  | { type: "error"; message?: string; at?: number };

export type DiscordLiveEvent = {
  id: string;
  kind: "activity" | "error";
  tool?: string;
  source?: string;
  message?: string;
  at: number;
};

export type ActivityItem =
  | { kind: "thinking"; id: string; content: string; at: number; steps?: ThinkingStep[]; request_id?: string }
  | { kind: "tool"; id: string; name: string; input: string; status: "running" | "done" | "error"; output?: string; at: number }
  | { kind: "memory"; id: string; memoryCount: number; at: number }
  | { kind: "error"; id: string; message: string; at: number };

export type ThinkingStep = {
  id: string;
  type: "thought" | "search" | "read" | "tool";
  content: string;
  status: "running" | "done" | "failed";
  at: number;
};

export type TaskPlanEntry = {
  id: string;
  at: number;
  plan: TaskPlanProjection;
  request_id?: string;
};

export type TimelineItem =
  | { kind: "message"; id: string; at: number; msg: Message }
  | { kind: "activity"; id: string; at: number; item: ActivityItem };

export type ProviderListItem = {
  id: string;
  name: string;
  local: boolean;
  description: string;
};

export type ProviderInfo = {
  provider: string;
  model: string;
  local: boolean;
  base_url?: string | null;
  available_providers: ProviderListItem[];
  context_window?: number;
  max_output_tokens?: number;
  ready?: boolean;
  readiness_message?: string;
  readiness_detail?: string;
  session_id?: string;
  binding_revision?: number;
};

export type ProviderModelsResponse = {
  provider: string;
  models: string[];
};

export type MemoryItem = {
  id: string;
  text: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
  memory_type?: string;
  pinned?: boolean;
  owner_id?: string;
  scope?: string;
  source_session_id?: string;
  source_execution_id?: string;
  source_item_id?: string;
  updated_at?: string;
  index_state?: string;
  supersedes?: string;
  /** Optional projection fields from MemoryCurator / list API. */
  project_id?: string;
  project_path?: string;
  confidence?: number | string;
  source?: string;
  status?: string;
  provenance?: string;
};

export type MemoryListResponse = {
  items: MemoryItem[];
  count: number;
  use_faiss: boolean;
};

export type MemoryDoctorReport = {
  ok: boolean;
  memory_count: number;
  scanned: number;
  use_faiss: boolean;
  auto_store_conversations: boolean;
  type_counts: Record<string, number>;
  pinned_count: number;
  profile_fact_count: number;
  missing_type_count: number;
  duplicate_groups: { count: number; preview?: string; items?: any[] }[];
  warnings: string[];
  recommendations: string[];
};

export type DocumentItem = {
  id: string;
  filename: string;
  chunks: number;
  source?: string;
  mime?: string;
  timestamp?: string;
};

export type DocumentListResponse = {
  items: DocumentItem[];
  count: number;
  enabled: boolean;
};

export type ThreadSessionState = OperationalThreadState & {
  thread_id: string;
  workspace_id?: string;
  active_project_id?: string;
  foreground_task_id?: string;
  suspended_task_ids?: string[];
  pending_approval_ids?: string[];
  source_metadata?: Record<string, any>;
  semantic_schema_version?: number;
  pending_approval_id?: string;
  last_execution_id?: string;
  last_trace_id?: string;
  runtime_provider?: string;
  selected_model_id?: string;
  active_turn_id?: string;
  model_profile?: Record<string, any>;
  context_budget?: Record<string, any>;
  updated_at: number;
};

export type ApprovalRecord = OperationalApproval & {
  id: string;
  thread_id: string;
  execution_id?: string | null;
  status: string;
  tool: string;
  kwargs: Record<string, any>;
  original_input: string;
  preview: string;
  summary: string;
  risk_level: string;
  policy_flags: string[];
  permission_level?: string;
  constraints?: string[];
  policy_snapshot?: Record<string, any>;
  retry_count?: number;
  execution_context?: Record<string, any>;
  session_permissions: Record<string, boolean>;
  dry_run_available: boolean;
  source: string;
  workspace_id: string;
  active_project_id: string;
  created_at: number;
  updated_at: number;
  decided_at?: number | null;
  outcome_summary: string;
};

export type ApprovalListResponse = {
  items: ApprovalRecord[];
  count: number;
};

export type PendingActionEnvelope = {
  has_pending: boolean;
  action?: ApprovalRecord | null;
  approval_id?: string | null;
  risk_level?: string | null;
  risk_color?: string | null;
  policy_flags?: string[];
  session_permissions?: Record<string, boolean>;
  dry_run_available?: boolean;
};

export type ApprovalDecisionEnvelope = {
  approval: ApprovalRecord;
  success: boolean;
  response: string;
  execution_id?: string | null;
  thread_state: ThreadSessionState;
};

export type ExecutionRecord = {
  id: string;
  request_id: string;
  kind: string;
  thread_id: string;
  source: string;
  status: string;
  query: string;
  workspace_id: string;
  active_project_id: string;
  runtime_provider: string;
  created_at: number;
  updated_at: number;
  completed_at?: number | null;
  success?: boolean | null;
  response_preview: string;
  error: string;
  approvals: string[];
  tools_used: string[];
  tool_latencies_ms: { tool: string; ms: number; error?: boolean }[];
  trace_id?: string | null;
  evaluation: Record<string, any>;
  metadata: Record<string, any>;
};

export type ExecutionListResponse = {
  items: ExecutionRecord[];
  count: number;
};

export type VisionAnalyzeResponse = {
  text: string;
  text_length: number;
  has_text: boolean;
  image_size: Record<string, number>;
};

export type AppState = {
  messages: Message[];
  streaming: boolean;
  listening: boolean;
  speaking: boolean;
  speechEnabled: boolean;
  speechBeat: number;
  addMessage: (msg: Message) => void;
  setStreaming: (v: boolean) => void;
  setListening: (v: boolean) => void;
  setSpeaking: (v: boolean) => void;
  setSpeechEnabled: (v: boolean) => void;
  bumpSpeechBeat: () => void;
};

export type AvatarConfig = {
  body_color: string;
  eye_color: string;
  bg_color: string;
  glow_color: string;
  idle_activity: string;
  breathing_speed: number;
  eye_size: number;
  body_roundness: number;
  enable_glow: boolean;
  enable_idle_activities: boolean;
  custom_status_text: string;
};

