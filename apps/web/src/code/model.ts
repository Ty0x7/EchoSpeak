export type CodeRuntime = {
  id: string; name: string; protocol: string; implemented: boolean; installed: boolean | null;
  authentication: string; can_start: boolean; blockers: string[]; description: string;
};
export type RuntimeCatalog = { schema_version: number; privacy_mode: string; runtimes: CodeRuntime[] };
export type AgentModel = { id: string; model: string; displayName?: string; isDefault?: boolean };
export function agentModels(value: unknown): AgentModel[] {
  if (!Array.isArray(value)) return [];
  return [...new Map(value.filter(row => row && typeof row.model === "string" && row.model.length > 0 && row.model.length <= 200)
    .slice(0, 200).map(row => [row.model, { id: String(row.id || row.model), model: row.model,
      displayName: typeof row.displayName === "string" ? row.displayName : undefined, isDefault: row.isDefault === true }])).values()];
}
export function agentModelName(models: AgentModel[], selected: string, reported: string): string {
  const id = selected || reported || models.find(row => row.isDefault)?.model || "";
  return models.find(row => row.model === id)?.displayName || id;
}
export type CodeSession = {
  thread_id: string; title: string; project_id: string; surface: string; agent_runtime: string;
  last_active_at: number; message_count: number;
};
export type CodeWorkspace = {
  schema_version: number;
  project: { id: string; name: string; workspace_root: string; trust_state: string };
  git_summary: string; sessions: CodeSession[]; can_start_native: boolean; native_blocker: string;
};

/** Runtime availability and project trust are independent; neither implies model access. */
export function canStartSession(runtime: CodeRuntime | undefined, workspace: CodeWorkspace | null): boolean {
  return Boolean(workspace?.can_start_native && runtime?.id === "echo" && runtime.implemented && runtime.can_start);
}

/** Each coding agent's display name; the harness itself is always the vendor's own. */
export function runtimeName(id: string | undefined): string {
  return ({ echo: "Echo native", codex: "Codex", claude_cli: "Claude Code", claude_code: "Claude Agent", acp: "ACP agent" } as Record<string, string>)[id || "echo"] || "ACP agent";
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
