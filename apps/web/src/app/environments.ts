/**
 * Chat, Agents and Code: three environments in one app, one sidebar.
 *
 * - Chat: talk to the model you chose (model-first; Echo's harness, no persona or team).
 * - Agents: message your persistent agents (Echo, Jarvis, GLaDOS, yours) and their groups.
 * - Code: projects and coding sessions (Echo native, Codex, Claude Code, ACP).
 *
 * The backend decides a conversation's environment (agent/environments.py) and sends it as
 * `environment`; the fallback here only covers rows built locally before that arrives.
 */
import type { SidebarPage } from "../components/ProjectSidebar";

export type Environment = "chat" | "agents" | "code";
export const ENVIRONMENTS: { id: Environment; label: string; hint: string }[] = [
  { id: "chat", label: "Chat", hint: "Talk to the model you choose" },
  { id: "agents", label: "Agents", hint: "Message Echo and your other agents" },
  { id: "code", label: "Code", hint: "Projects and coding sessions" },
];

type ThreadLike = { id: string; surface?: string; environment?: string };

export function isEnvironment(value: unknown): value is Environment {
  return value === "chat" || value === "agents" || value === "code";
}

export function environmentOf(thread: ThreadLike | undefined, roomThreadIds: Set<string>): Environment {
  if (!thread) return "chat";
  if (isEnvironment(thread.environment)) return thread.environment;
  if (thread.surface === "code") return "code";
  return roomThreadIds.has(thread.id) ? "agents" : "chat";
}

/** The sidebar's pages, by environment. Each page lives in exactly one environment. */
export const ENVIRONMENT_PAGES: Record<Environment, Exclude<SidebarPage, "chat" | "code" | "agents">[]> = {
  chat: ["projects", "creations", "artifacts"],
  agents: ["groups", "routines"],
  code: [],
};

export function environmentForPage(page: SidebarPage): Environment | null {
  if (page === "code") return "code";
  if (page === "agents") return "agents";
  for (const env of ["chat", "agents"] as const) if ((ENVIRONMENT_PAGES[env] as string[]).includes(page)) return env;
  return null;
}

export const NEW_LABEL: Record<Environment, string> = { chat: "New chat", agents: "New agent", code: "New coding session" };

const KEY = "echospeak.environment";

export function loadEnvironment(storage: Pick<Storage, "getItem"> | null): Environment {
  try {
    const value = storage?.getItem(KEY);
    return isEnvironment(value) ? value : "chat";
  } catch {
    return "chat";
  }
}

export function saveEnvironment(storage: Pick<Storage, "setItem"> | null, value: Environment): void {
  try { storage?.setItem(KEY, value); } catch { /* storage unavailable */ }
}

/** Chat shows the model, not an agent: "gpt-5.5" → "GPT-5.5", "claude-sonnet-5-5" → "Claude Sonnet 5.5". */
export function modelDisplayName(model: string): string {
  const id = String(model || "").split("/").pop() || "";
  if (!id) return "Assistant";
  if (/^gpt-/i.test(id)) return "GPT-" + id.slice(4);
  if (/^claude-/i.test(id)) {
    const words = id.slice(7).split("-");
    const name = words.filter(word => !/^\d+$/.test(word)).map(word => word[0].toUpperCase() + word.slice(1));
    const version = words.filter(word => /^\d+$/.test(word) && word.length <= 2).join(".");
    return ["Claude", ...name, version].filter(Boolean).join(" ");
  }
  return id;
}

/**
 * Chat → Agents without copying the conversation: one excerpt, chosen and sent by the owner,
 * becomes a draft in the agent's conversation. The agent gets only what the owner sends.
 */
export function handoffDraft(chatTitle: string, excerpt: string, limit = 4000): string {
  const text = String(excerpt || "").trim();
  const clipped = text.length > limit ? text.slice(0, limit).trimEnd() + "…" : text;
  const quoted = clipped.split("\n").map(line => `> ${line}`).join("\n");
  return `From my chat “${chatTitle || "Chat"}”:\n${quoted}\n\n`;
}
