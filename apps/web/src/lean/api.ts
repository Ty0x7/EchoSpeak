import type { ChatSearchHit } from "../components/ProjectSidebar";
import type {
  LeanPersona,
  LeanRoom,
  LearningEpisode,
  LearningEvent,
  LearningLesson,
  LearningProfile,
  LearningStatus,
  ReliabilityRow,
} from "./types";

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = String(body?.detail || detail);
    } catch {
      // keep status text
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const leanApi = (apiBase: string) => ({
  async searchChats(query: string): Promise<ChatSearchHit[]> {
    const data = await json<{ items: ChatSearchHit[] }>(await fetch(`${apiBase}/lean/search?q=${encodeURIComponent(query)}&limit=20`));
    return data.items || [];
  },
  async agents(): Promise<LeanPersona[]> {
    const data = await json<{ items: LeanPersona[] }>(await fetch(`${apiBase}/lean/agents`));
    return data.items || [];
  },
  async createAgent(payload: Partial<LeanPersona>): Promise<LeanPersona> {
    return json(await fetch(`${apiBase}/lean/agents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
  },
  async updateAgent(id: string, payload: Partial<LeanPersona>): Promise<LeanPersona> {
    return json(await fetch(`${apiBase}/lean/agents/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
  },
  async deleteAgent(id: string): Promise<void> {
    await json(await fetch(`${apiBase}/lean/agents/${encodeURIComponent(id)}`, { method: "DELETE" }));
  },
  async rooms(): Promise<LeanRoom[]> {
    const data = await json<{ items: LeanRoom[] }>(await fetch(`${apiBase}/lean/rooms`));
    return data.items || [];
  },
  async createRoom(payload: { name: string; agent_ids: string[]; kind?: string; mode?: string; max_messages?: number }): Promise<LeanRoom> {
    return json(await fetch(`${apiBase}/lean/rooms`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
  },
  async updateRoom(id: string, payload: { name?: string; agent_ids?: string[]; mode?: string; max_messages?: number }): Promise<LeanRoom> {
    return json(await fetch(`${apiBase}/lean/rooms/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
  },
  async deleteRoom(id: string): Promise<void> {
    await json(await fetch(`${apiBase}/lean/rooms/${encodeURIComponent(id)}`, { method: "DELETE" }));
  },
  /** allow / deny / always for an approval, or the answer to an ask_user question. */
  async decide(approvalId: string, decision: string): Promise<void> {
    await json(await fetch(`${apiBase}/lean/approvals/${encodeURIComponent(approvalId)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) }));
  },
  async toolsets(): Promise<{ id: string; tools: string[] }[]> {
    const data = await json<{ toolsets: { id: string; tools: string[] }[] }>(await fetch(`${apiBase}/lean/toolsets`));
    return data.toolsets || [];
  },
  // ── learning ──────────────────────────────────────────────────────────
  /** Worked (1) / Didn't work (-1) / take it back (0) on a reply. */
  async feedback(executionId: string, value: -1 | 0 | 1, note = "", agentId = ""): Promise<void> {
    await json(await fetch(`${apiBase}/lean/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ execution_id: executionId, value, note, agent_id: agentId }) }));
  },
  async learningStatus(): Promise<LearningStatus> {
    return json(await fetch(`${apiBase}/lean/learning/status`));
  },
  async learningProfiles(): Promise<LearningProfile[]> {
    const data = await json<{ profiles: LearningProfile[] }>(await fetch(`${apiBase}/lean/learning/profiles`));
    return data.profiles || [];
  },
  async lessons(params: { agentId?: string; status?: string } = {}): Promise<LearningLesson[]> {
    const query = new URLSearchParams();
    if (params.agentId) query.set("agent_id", params.agentId);
    if (params.status) query.set("status", params.status);
    const data = await json<{ lessons: LearningLesson[] }>(await fetch(`${apiBase}/lean/learning/lessons?${query}`));
    return data.lessons || [];
  },
  async lesson(id: string): Promise<{ lesson: LearningLesson | null; events: LearningEvent[]; episodes: LearningEpisode[] }> {
    return json(await fetch(`${apiBase}/lean/learning/lessons/${encodeURIComponent(id)}`));
  },
  async lessonAction(id: string, action: "approve" | "reject" | "promote" | "retire" | "restore"): Promise<LearningLesson> {
    const data = await json<{ lesson: LearningLesson }>(await fetch(`${apiBase}/lean/learning/lessons/${encodeURIComponent(id)}/action`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }));
    return data.lesson;
  },
  async editLesson(id: string, patch: { title?: string; text?: string }): Promise<LearningLesson> {
    const data = await json<{ lesson: LearningLesson }>(await fetch(`${apiBase}/lean/learning/lessons/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }));
    return data.lesson;
  },
  async rollbackLesson(id: string, eventId: number): Promise<LearningLesson | null> {
    const data = await json<{ lesson: LearningLesson | null }>(await fetch(`${apiBase}/lean/learning/lessons/${encodeURIComponent(id)}/rollback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event_id: eventId }) }));
    return data.lesson;
  },
  async deleteLesson(id: string): Promise<void> {
    await json(await fetch(`${apiBase}/lean/learning/lessons/${encodeURIComponent(id)}`, { method: "DELETE" }));
  },
  async episodes(agentId = "", limit = 30): Promise<LearningEpisode[]> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (agentId) query.set("agent_id", agentId);
    const data = await json<{ episodes: LearningEpisode[] }>(await fetch(`${apiBase}/lean/learning/episodes?${query}`));
    return data.episodes || [];
  },
  async reliability(): Promise<Record<string, ReliabilityRow[]>> {
    return json(await fetch(`${apiBase}/lean/learning/reliability`));
  },
  async pauseLearning(agentId: string, paused: boolean): Promise<void> {
    await json(await fetch(`${apiBase}/lean/learning/agents/${encodeURIComponent(agentId)}/pause`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paused }) }));
  },
  async reflectNow(): Promise<{ pending: number }> {
    return json(await fetch(`${apiBase}/lean/learning/reflect`, { method: "POST" }));
  },
});
