import type { LeanPersona, LeanRoom } from "./types";

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
  async createRoom(payload: { name: string; agent_ids: string[]; kind?: string }): Promise<LeanRoom> {
    return json(await fetch(`${apiBase}/lean/rooms`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
  },
  async updateRoom(id: string, payload: { name?: string; agent_ids?: string[] }): Promise<LeanRoom> {
    return json(await fetch(`${apiBase}/lean/rooms/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
  },
  async deleteRoom(id: string): Promise<void> {
    await json(await fetch(`${apiBase}/lean/rooms/${encodeURIComponent(id)}`, { method: "DELETE" }));
  },
  async decide(approvalId: string, decision: "allow" | "deny" | "always"): Promise<void> {
    await json(await fetch(`${apiBase}/lean/approvals/${encodeURIComponent(approvalId)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) }));
  },
  async toolsets(): Promise<{ id: string; tools: string[] }[]> {
    const data = await json<{ toolsets: { id: string; tools: string[] }[] }>(await fetch(`${apiBase}/lean/toolsets`));
    return data.toolsets || [];
  },
});
