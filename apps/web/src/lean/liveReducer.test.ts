import { describe, expect, it } from "vitest";
import { emptyLive, isLeanEvent, leanReducer, messageFromTimeline } from "./liveReducer";
import type { LeanEvent } from "./types";

const run = (events: LeanEvent[]) => events.reduce(leanReducer, emptyLive("r1"));
const base = { request_id: "r1", message_id: "m1", agent_id: "echo" };

describe("lean live reducer", () => {
  it("keeps thinking, tools, and text in the order they streamed", () => {
    const state = run([
      { type: "agent_start", ...base, agent: { id: "echo", name: "Echo" } },
      { type: "reasoning_delta", ...base, step: 1, text: "plan " },
      { type: "reasoning_delta", ...base, step: 1, text: "it" },
      { type: "tool_start", ...base, step: 1, id: "t1", name: "web_search", label: "Searching" },
      { type: "tool_end", ...base, step: 1, id: "t1", name: "web_search", ok: true, output: "hits" },
      { type: "agent_token", ...base, step: 2, data: "Hello " },
      { type: "agent_token", ...base, step: 2, data: "there" },
      { type: "agent_done", ...base, text: "Hello there", success: true },
    ]);
    const msg = state.messages.m1;
    expect(msg.segments.map((s) => s.kind)).toEqual(["thinking", "tool", "text"]);
    expect([msg.segments[0].kind, (msg.segments[0] as { text: string }).text]).toEqual(["thinking", "plan it"]);
    expect(Boolean((msg.segments[0] as { endedAt?: number }).endedAt)).toBe(true);
    expect([(msg.segments[1] as { status: string }).status, (msg.segments[1] as { output: string }).output]).toEqual(["done", "hits"]);
    expect((msg.segments[2] as { text: string }).text).toBe("Hello there");
    expect(msg.status).toBe("done");
  });

  it("separates replies from different agents in one turn", () => {
    const state = run([
      { type: "agent_start", ...base, agent: { id: "scout", name: "Scout" } },
      { type: "agent_token", ...base, step: 1, data: "A" },
      { type: "agent_start", request_id: "r1", message_id: "m2", agent_id: "forge", agent: { id: "forge", name: "Forge" } },
      { type: "agent_token", request_id: "r1", message_id: "m2", agent_id: "forge", step: 1, data: "B" },
    ]);
    expect(state.order).toEqual(["m1", "m2"]);
    expect(state.messages.m2.agent.name).toBe("Forge");
  });

  it("tracks approvals through their decision", () => {
    const state = run([
      { type: "agent_start", ...base, agent: { id: "echo", name: "Echo" } },
      { type: "approval_request", ...base, id: "a1", tool: "file_delete", summary: "Deleting x", reason: "danger" },
      { type: "approval_resolved", ...base, id: "a1", decision: "allow" },
    ]);
    const seg = state.messages.m1.segments[0] as { kind: string; decision: string };
    expect([seg.kind, seg.decision]).toEqual(["approval", "allow"]);
  });

  it("only claims lean events", () => {
    expect(isLeanEvent({ type: "agent_token", data: "x" })).toBe(false);
    expect(isLeanEvent({ type: "agent_token", message_id: "m", data: "x" })).toBe(true);
    expect(isLeanEvent({ type: "final", runtime: "lean" })).toBe(true);
    expect(isLeanEvent({ type: "final" })).toBe(false);
  });

  it("rebuilds a persisted timeline after reload", () => {
    const msg = messageFromTimeline({
      messageId: "m1",
      agentId: "echo",
      agentName: "Echo",
      text: "Answer",
      at: 1000,
      timeline: [
        { kind: "thinking", step: 1, text: "hmm", at: 1, ended_at: 4 },
        { kind: "tool", step: 1, id: "t", name: "web_search", label: "Searching", status: "done", output: "o", at: 4 },
        { kind: "text", step: 2, text: "Answer", at: 6 },
      ],
    });
    expect(msg.segments.map((s) => s.kind)).toEqual(["thinking", "tool", "text"]);
    const thought = msg.segments[0] as { startedAt: number; endedAt?: number };
    expect((thought.endedAt || 0) - thought.startedAt).toBe(3000);
  });
});
