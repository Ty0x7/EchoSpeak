export type CodeRuntime = {
  id: string; name: string; protocol: string; implemented: boolean; installed: boolean | null;
  authentication: string; can_start: boolean; blockers: string[]; description: string;
};
export type RuntimeCatalog = { schema_version: number; privacy_mode: string; runtimes: CodeRuntime[] };
export type AgentModel = { id: string; model: string; displayName?: string; isDefault?: boolean; description?: string;
  /** The agent's own effort levels for this model (Codex model/list; Claude Code --effort), and its default. */
  efforts?: string[]; defaultEffort?: string };
/** What Echo last saw of a coding agent's own sign-in (no secrets): GET /echo-code/runtimes/{id}/account. */
export type AgentAccount = {
  installed?: boolean; signed_in?: boolean; method?: string; plan?: string; models?: AgentModel[];
  problem?: string; checked_at?: number; stale?: boolean;
  subscription_usage?: import("./RuntimeUsage").SubscriptionUsage; model_problem?: string;
};
/** "Plus plan", "Pro plan", or "" when the agent didn't report one. */
export function planLabel(account: AgentAccount | null | undefined): string {
  const plan = String(account?.plan || "").trim();
  return plan ? `${plan[0].toUpperCase()}${plan.slice(1)} plan` : "";
}
export function agentModels(value: unknown): AgentModel[] {
  if (!Array.isArray(value)) return [];
  return [...new Map(value.filter(row => row && typeof row.model === "string" && row.model.length > 0 && row.model.length <= 200)
    .slice(0, 200).map(row => [row.model, { id: String(row.id || row.model), model: row.model,
      displayName: typeof row.displayName === "string" ? row.displayName : undefined, isDefault: row.isDefault === true,
      description: typeof row.description === "string" ? row.description : undefined,
      efforts: Array.isArray(row.efforts) ? row.efforts.filter((e: unknown) => typeof e === "string" && /^[a-z_]{1,20}$/.test(e)).slice(0, 8) : [],
      defaultEffort: typeof row.defaultEffort === "string" ? row.defaultEffort : "" }])).values()];
}
/** The model a session will use: the one chosen, the one the agent reported, or its default. */
export function agentModelRow(models: AgentModel[], selected: string, reported: string): AgentModel | undefined {
  const id = selected || reported || models.find(row => row.isDefault)?.model || "";
  return models.find(row => row.model === id) || models.find(row => row.isDefault);
}
export function agentModelName(models: AgentModel[], selected: string, reported: string): string {
  const id = selected || reported || models.find(row => row.isDefault)?.model || "";
  return models.find(row => row.model === id)?.displayName || id;
}
export type InboxState = "working" | "needs_attention" | "ready_to_review" | "settled" | "snoozed";
export type InboxSummary = { state: InboxState; reason: string; activity: string; verification: string; updated_at: number; can_triage: boolean; snoozed_until?: number; woke?: boolean; settled_at?: number };
export const INBOX_STATES: InboxState[] = ["working", "needs_attention", "ready_to_review", "settled", "snoozed"];
export const INBOX_LABELS: Record<InboxState, string> = { working: "Working", needs_attention: "Needs attention", ready_to_review: "Ready to review", settled: "Settled", snoozed: "Snoozed" };

/** Server evidence wins over the optimistic local spinner (an approval is attention). */
export function inboxState(inbox?: InboxSummary, locallyWorking = false): InboxState {
  if (inbox?.state === "needs_attention") return "needs_attention";
  return locallyWorking ? "working" : inbox?.state || "settled";
}

export type CodeSession = {
  thread_id: string; title: string; project_id: string; surface: string; agent_runtime: string;
  last_active_at: number; message_count: number;
  inbox?: InboxSummary;
};
export type CodeWorkspace = {
  schema_version: number;
  project: { id: string; name: string; workspace_root: string; trust_state: string };
  git_summary: string; sessions: CodeSession[]; can_start_native: boolean; native_blocker: string;
  external_blocker?: string;
  worktree_available?: boolean; worktree_unavailable?: string;
};

/** Runtime availability and project trust are independent; neither implies model access. */
export function canStartSession(runtime: CodeRuntime | undefined, workspace: CodeWorkspace | null): boolean {
  return Boolean(workspace?.can_start_native && runtime?.id === "echo" && runtime.implemented && runtime.can_start);
}

/** Each coding agent's display name; the harness itself is always the vendor's own. */
export function runtimeName(id: string | undefined): string {
  return ({ echo: "Echo native", codex: "Codex", claude_cli: "Claude Code", acp: "ACP agent" } as Record<string, string>)[id || "echo"] || "ACP agent";
}

export function runtimeStatus(runtime: CodeRuntime): string {
  if (runtime.implemented) return runtime.can_start ? "Available · own account" : "Setup required";
  return runtime.installed ? "CLI found · execution planned" : runtime.installed === false ? "CLI not found · execution planned" : "Planned";
}

export class CodeRequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function createCodeSession(apiBase: string, projectId: string, runtimeId: string, key: string): Promise<CodeSession> {
  const response = await fetch(`${apiBase}/echo-code/projects/${encodeURIComponent(projectId)}/sessions`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ runtime_id: runtimeId, idempotency_key: key }),
  });
  const data = await response.json();
  if (!response.ok) throw new CodeRequestError(typeof data.detail === "string" ? data.detail : "Could not create the coding session", response.status);
  return data;
}
