import { describe, expect, it } from "vitest";
import {
  identityParts, inScope, loadAssistantMode, loadOverview, scopes, trustQuestion, visibleRows, who, type OverviewRow,
} from "./sessionOverview";

const WORK = { id: "p1", name: "Work", root: "/home/dev/Work", trusted: true };
const row = (id: string, title: string, extra: Partial<OverviewRow> = {}): OverviewRow => ({
  thread_id: id, title, last_active_at: 0, message_count: 2, kind: "echo",
  agents: [{ id: "echo", name: "Echo" }], project_id: "", project: null, status: "idle", ...extra,
});
const ROWS: OverviewRow[] = [
  row("t-oilers", "When do the Oilers play next", { project_id: "p1", project: WORK, preview: "Tuesday at Florida." }),
  row("t-pong", "Pong in a single file", { kind: "agent", agents: [{ id: "forge", name: "Glados" }], status: "needs_answer" }),
  row("t-crew", "Release notes", { kind: "group", room_name: "Release crew", agents: [{ id: "scout", name: "Jarvis" }, { id: "forge", name: "Glados" }], project_id: "p1", project: WORK }),
  row("t-hockey", "Hockey chat", { preview: "the Oilers won" }),
  row("t-old", "Archived", { archived: true }),
];

describe("the shared conversation list", () => {
  it("says who answers in each conversation", () => {
    expect(ROWS.map(who)).toEqual(["Echo", "Glados", "Release crew", "Echo", "Echo"]);
  });

  it("filters the way the terminal does", () => {
    expect(scopes("")).toEqual(["all", "echo", "agents"]);
    expect(scopes("p1")).toEqual(["all", "echo", "agents", "project"]);
    const ids = (scope: Parameters<typeof inScope>[1]) => visibleRows(ROWS, scope, "p1", "").map(r => r.thread_id);
    expect(ids("all")).toEqual(["t-oilers", "t-pong", "t-crew", "t-hockey"]);   // archived stays out
    expect(ids("echo")).toEqual(["t-oilers", "t-hockey"]);
    expect(ids("agents")).toEqual(["t-pong", "t-crew"]);
    expect(ids("project")).toEqual(["t-oilers", "t-crew"]);
  });

  it("searches titles, agents, group members and replies, titles first", () => {
    const ids = (q: string) => visibleRows(ROWS, "all", "", q).map(r => r.thread_id);
    expect(ids("glados")).toEqual(["t-pong", "t-crew"]);
    expect(ids("oilers")).toEqual(["t-oilers", "t-hockey"]);     // the title match before the reply match
    expect(ids("tuesday")).toEqual(["t-oilers"]);
    expect(ids("needs your answer")).toEqual(["t-pong"]);
    expect(ids("work release")).toEqual(["t-crew"]);
    expect(ids("zzz")).toEqual([]);
  });

  it("falls back to the old lists on a backend without the overview", async () => {
    const missing = async () => new Response("", { status: 404 });
    expect(await loadOverview("http://x", missing)).toBeNull();
    const ok = async () => new Response(JSON.stringify({ items: ROWS.slice(0, 1) }), { status: 200 });
    expect((await loadOverview("http://x", ok))?.[0].thread_id).toBe("t-oilers");
    const broken = async () => new Response("", { status: 500 });
    await expect(loadOverview("http://x", broken)).rejects.toThrow("Conversations failed (500)");
  });

  it("is unified unless the backend says otherwise", async () => {
    const facts = (mode: string) => async () => new Response(JSON.stringify({ assistant_mode: mode }), { status: 200 });
    expect(await loadAssistantMode("http://x", facts("unified"))).toBe("unified");
    expect(await loadAssistantMode("http://x", facts("environments"))).toBe("environments");
    expect(await loadAssistantMode("http://x", async () => new Response("", { status: 404 }))).toBe("environments");
    expect(await loadAssistantMode("http://x", async () => { throw new Error("offline"); })).toBe("environments");
  });
});

describe("the conversation header", () => {
  it("names the agent, the model, the project and its trust, and the approval mode", () => {
    const parts = identityParts({ agent: "Echo", model: "ollama/qwen3.5:9b", project: { name: "Work", trusted: true }, approvalMode: "smart" });
    expect(parts.map(p => p.text)).toEqual(["Echo", "qwen3.5:9b", "Work", "Ask for risky actions"]);
    const readOnly = identityParts({ agent: "Jarvis", model: "", project: { name: "Notes", trusted: false } });
    expect(readOnly.map(p => p.text)).toEqual(["Jarvis", "Notes (read-only)"]);
  });

  it("asks before trusting a folder, and says what trust means", () => {
    const text = trustQuestion("/home/dev/Work/shop/");
    expect(text).toContain("“shop”");
    expect(text).toContain("edit files and run commands");
    expect(text).toContain("read-only");
  });
});
