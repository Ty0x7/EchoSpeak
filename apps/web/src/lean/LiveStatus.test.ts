import { describe, expect, it } from "vitest";
import { describeLive } from "./LiveStatus";
import { emptyLive, leanReducer, messageFromTimeline } from "./liveReducer";
import type { LeanEvent } from "./types";

const run = (events: LeanEvent[]) => events.reduce(leanReducer, emptyLive("r1"));
const ev = (message_id: string, agent_id: string, rest: Record<string, any>): LeanEvent => ({ request_id: "r1", message_id, agent_id, ...rest }) as LeanEvent;

describe("multi-agent live state", () => {
  it("keeps parallel agents' interleaved events in their own messages", () => {
    const state = run([
      ev("s1", "scout", { type: "agent_start", agent: { id: "scout", name: "Scout" }, parallel: true }),
      ev("f1", "forge", { type: "agent_start", agent: { id: "forge", name: "Forge" }, parallel: true }),
      ev("f1", "forge", { type: "agent_token", step: 1, data: "B " }),
      ev("s1", "scout", { type: "agent_token", step: 1, data: "A " }),
      ev("f1", "forge", { type: "agent_token", step: 1, data: "is simpler" }),
      ev("s1", "scout", { type: "agent_token", step: 1, data: "is faster" }),
    ]);
    expect(state.order).toEqual(["s1", "f1"]);
    expect(state.messages.s1.segments.map((s) => (s as { text: string }).text)).toEqual(["A is faster"]);
    expect(state.messages.f1.segments.map((s) => (s as { text: string }).text)).toEqual(["B is simpler"]);
    expect(describeLive(state).activity).toBe("Scout and Forge are working");
    expect(describeLive(state).agents.map((a) => a.id)).toEqual(["scout", "forge"]);
  });

  it("marks handed-off work and the merge summary", () => {
    const state = run([
      ev("b1", "scout", { type: "agent_start", agent: { id: "scout", name: "Scout" }, delegated_by: { id: "echo", name: "Echo" } }),
      ev("b1", "scout", { type: "tool_start", step: 1, id: "t1", name: "web_search", label: "Searching “owls”" }),
    ]);
    expect(state.messages.b1.delegatedBy).toBe("Echo");
    expect(describeLive(state).activity).toBe("Echo → Scout · Searching “owls”");

    const merged = run([ev("m1", "echo", { type: "agent_start", agent: { id: "echo", name: "Echo" }, role: "merge" })]);
    expect(merged.messages.m1.role).toBe("merge");
    expect(describeLive(merged).activity).toBe("Echo is summarizing");
  });

  it("restores handoff and summary labels from history", () => {
    const msg = messageFromTimeline({
      messageId: "b1", agentId: "scout", agentName: "Scout", text: "Found it.", timeline: [], at: 1,
      delegatedBy: "Echo", role: "merge",
    });
    expect([msg.delegatedBy, msg.role]).toEqual(["Echo", "merge"]);
  });

  it("flags an approval wait even while others keep working", () => {
    const state = run([
      ev("s1", "scout", { type: "agent_start", agent: { id: "scout", name: "Scout" } }),
      ev("f1", "forge", { type: "agent_start", agent: { id: "forge", name: "Forge" } }),
      ev("f1", "forge", { type: "approval_request", step: 1, id: "a1", tool: "terminal_run", summary: "rm -rf build", reason: "deletes" }),
    ]);
    const live = describeLive(state);
    expect([live.needsOk, live.activity]).toEqual([true, "Forge needs your OK"]);
  });
});
