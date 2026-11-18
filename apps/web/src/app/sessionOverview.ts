/**
 * One list of conversations, described the same way in the desktop app and the terminal.
 *
 * The backend describes every conversation once (`GET /sessions/overview`, agent/session_overview.py):
 * who answers there (Echo, an agent, a group), the attached project, its state and its last exchange.
 * The terminal's session manager (apps/terminal/echospeak_cli/tui/sessions.py) and this sidebar use the
 * same rows, the same filters (All / Echo / Agents / This project) and the same search order, so the two
 * interfaces never disagree about what a conversation is.
 *
 * In unified mode (the default, `GET /app/facts`), this replaces the Chat / Agents / Code switch: they are
 * capabilities of one assistant, not places to choose between.
 */

export type OverviewRow = {
  thread_id: string;
  title: string;
  created_at?: number;
  last_active_at: number;
  message_count: number;
  kind: "echo" | "agent" | "group";
  agents: { id: string; name: string }[];
  room_name?: string;
  project_id: string;
  project: { id: string; name: string; root?: string; trusted?: boolean } | null;
  status: "idle" | "running" | "needs_answer" | "needs_approval" | "failed" | string;
  last_request?: string;
  preview?: string;
  model?: string;
  environment?: string;
  archived?: boolean;
};

export type Scope = "all" | "echo" | "agents" | "project";
export type AssistantMode = "unified" | "environments";

export const SCOPE_LABEL: Record<Scope, string> = { all: "All", echo: "Echo", agents: "Agents", project: "This project" };

export const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  needs_answer: "Needs your answer",
  needs_approval: "Needs approval",
  failed: "Last run failed",
};

/** The filters that would show something different: no "This project" without one. */
export function scopes(projectId: string): Scope[] {
  return projectId ? ["all", "echo", "agents", "project"] : ["all", "echo", "agents"];
}

/** Who answers in this conversation: "Echo", "Jarvis", or the group's name. */
export function who(row: OverviewRow): string {
  if (row.room_name) return row.room_name;
  const names = (row.agents || []).map(agent => agent.name).filter(Boolean);
  return names.join(", ") || (row.kind === "echo" ? "Echo" : "Agent");
}

export function inScope(row: OverviewRow, scope: Scope, projectId: string): boolean {
  if (scope === "echo") return row.kind === "echo";
  if (scope === "agents") return row.kind === "agent" || row.kind === "group";
  if (scope === "project") return Boolean(projectId) && row.project_id === projectId;
  return true;
}

function words(query: string): string[] {
  return query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
}

function named(row: OverviewRow): string {
  const members = (row.agents || []).map(agent => agent.name).join(" ");
  return [row.title, who(row), members, row.project?.name || ""].join(" ").toLocaleLowerCase();
}

export function matches(row: OverviewRow, query: string): boolean {
  const hay = [named(row), row.last_request || "", row.preview || "", STATUS_LABEL[row.status] || ""].join(" ").toLocaleLowerCase();
  return words(query).every(word => hay.includes(word));
}

/**
 * The rows to show: in scope, matching the search, a conversation called what you typed before one
 * that only mentions it in its last reply (each group keeps its activity order), like the terminal.
 */
export function visibleRows(rows: OverviewRow[], scope: Scope, projectId: string, query: string): OverviewRow[] {
  const shown = rows.filter(row => !row.archived && inScope(row, scope, projectId) && matches(row, query));
  if (!words(query).length) return shown;
  const isNamed = (row: OverviewRow) => words(query).every(word => named(row).includes(word));
  return [...shown.filter(isNamed), ...shown.filter(row => !isNamed(row))];
}

type Fetcher = (url: string, init?: RequestInit, timeoutMs?: number) => Promise<Response>;

/** The conversations, or null when the backend predates the overview (then the old lists stay). */
export async function loadOverview(apiBase: string, fetcher: Fetcher, limit = 200): Promise<OverviewRow[] | null> {
  const response = await fetcher(`${apiBase}/sessions/overview?limit=${limit}`, undefined, 6000);
  if (response.status === 404 || response.status === 405) return null;
  if (!response.ok) throw new Error(`Conversations failed (${response.status})`);
  const data = await response.json();
  return Array.isArray(data?.items) ? (data.items as OverviewRow[]) : [];
}

/** One assistant unless the owner chose the legacy layout; an older backend means the legacy layout. */
export async function loadAssistantMode(apiBase: string, fetcher: Fetcher): Promise<AssistantMode> {
  try {
    const response = await fetcher(`${apiBase}/app/facts`, undefined, 6000);
    if (!response.ok) return "environments";
    const data = await response.json();
    return data?.assistant_mode === "environments" ? "environments" : "unified";
  } catch {
    return "environments";
  }
}

export const APPROVAL_LABEL: Record<string, string> = {
  smart: "Ask for risky actions",
  autonomous: "Autonomous",
  always: "Ask before every action",
  never: "Never ask",
};

/**
 * What the conversation header says, in the order the terminal's footer uses: who answers, the model,
 * the project (and whether Echo may change it), the approval mode.
 */
export function identityParts(args: {
  agent: string; model: string; project?: { name: string; trusted?: boolean } | null; approvalMode?: string;
}): { key: string; text: string; hint: string }[] {
  const parts = [{ key: "agent", text: args.agent || "Echo", hint: "Who answers in this conversation" }];
  const model = String(args.model || "").split("/").pop() || "";
  if (model) parts.push({ key: "model", text: model, hint: "The model writing the replies" });
  if (args.project?.name) {
    parts.push({
      key: "project",
      text: args.project.name + (args.project.trusted ? "" : " (read-only)"),
      hint: args.project.trusted
        ? "Trusted workspace. File edits and commands also require the active agent’s tools and your permission settings."
        : "Attached, not trusted: it can be read but not changed",
    });
  }
  if (args.approvalMode) {
    parts.push({ key: "approvals", text: APPROVAL_LABEL[args.approvalMode] || args.approvalMode, hint: "Controls when actions ask for approval. Available tools depend on the agent and Settings › Permissions." });
  }
  return parts;
}

/** The trust question asked when a folder is attached, as the terminal asks it. */
export function trustQuestion(path: string): string {
  const name = String(path || "").replace(/\/+$/, "").split("/").pop() || path;
  return `Let Echo work in “${name}”?\n\nOK: trusted. Echo can edit files and run commands there (your permission switches and approvals still apply).\nCancel: attach it read-only.`;
}
