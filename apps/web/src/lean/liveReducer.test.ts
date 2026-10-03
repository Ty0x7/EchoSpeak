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

  it("shows A -> B -> A as three messages in the order they happened", () => {
    const a1 = { request_id: "r1", message_id: "a1", agent_id: "echo" };
    const b = { request_id: "r1", message_id: "b1", agent_id: "scout" };
    const a2 = { request_id: "r1", message_id: "a2", agent_id: "echo" };
    const state = run([
      { type: "agent_start", ...a1, agent: { id: "echo", name: "Echo" } },
      { type: "agent_token", ...a1, step: 1, data: "Asking Scout." },
      { type: "tool_start", ...a1, step: 1, id: "d1", name: "delegate_to_agent", label: "Handing to Scout" },
      { type: "tool_end", ...a1, step: 1, id: "d1", name: "delegate_to_agent", ok: true, output: "Handed to Scout." },
      { type: "agent_done", ...a1, text: "Asking Scout.", success: true, handoff: true },
      { type: "delegation", request_id: "r1", from: "echo", to: "scout" },
      { type: "agent_start", ...b, agent: { id: "scout", name: "Scout" } },
      { type: "reasoning_delta", ...b, step: 1, text: "looking" },
      { type: "agent_token", ...b, step: 1, data: "Found it." },
      { type: "agent_done", ...b, text: "Found it.", success: true },
      { type: "agent_start", ...a2, agent: { id: "echo", name: "Echo" }, continues: true },
      { type: "reasoning_delta", ...a2, step: 2, text: "wrap up" },
      { type: "agent_token", ...a2, step: 2, data: "Done." },
      { type: "agent_done", ...a2, text: "Done.", success: true },
    ]);
    expect(state.order).toEqual(["a1", "b1", "a2"]);
    expect(state.order.map((id) => state.messages[id].agent.id)).toEqual(["echo", "scout", "echo"]);
    expect(state.order.map((id) => state.messages[id].text)).toEqual(["Asking Scout.", "Found it.", "Done."]);
    // Each message carries only its own thinking and tools.
    expect(state.messages.a1.segments.map((s) => s.kind)).toEqual(["text", "tool"]);
    expect(state.messages.b1.segments.map((s) => s.kind)).toEqual(["thinking", "text"]);
    expect(state.messages.a2.segments.map((s) => s.kind)).toEqual(["thinking", "text"]);
    expect(state.order.every((id) => state.messages[id].status === "done")).toBe(true);
  });

  it("drops a continuation that ended with nothing to add", () => {
    const a2 = { request_id: "r1", message_id: "a2", agent_id: "echo" };
    const state = run([
      { type: "agent_start", ...base, agent: { id: "scout", name: "Scout" } },
      { type: "agent_done", ...base, text: "Found it.", success: true },
      { type: "agent_start", ...a2, agent: { id: "echo", name: "Echo" }, continues: true },
      { type: "agent_done", ...a2, text: "", success: true, empty: true },
    ]);
    expect(state.order).toEqual(["m1"]);
    expect("a2" in state.messages).toBe(false);
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

  it("puts a group job's outcome and continuation notes under the run's last message", () => {
    const m2 = { request_id: "r1", message_id: "m2", agent_id: "forge" };
    const state = run([
      { type: "agent_start", ...base, agent: { id: "echo", name: "Echo" } },
      { type: "agent_done", ...base, text: "Asked Glados.", success: true },
      { type: "agent_start", ...m2, agent: { id: "forge", name: "Glados" } },
      { type: "agent_token", ...m2, step: 1, data: "Wrote it." },
      { type: "agent_done", ...m2, text: "Wrote it.", success: true },
      { type: "job_continue", request_id: "r1", reason: "no tests yet", agent: "Glados", round: 2 },
      { type: "run_outcome", request_id: "r1", status: "done", summary: "hello.py written and tested." },
    ]);
    expect(isLeanEvent({ type: "run_outcome" })).toBe(true);
    expect(state.messages.m1.outcome).toBe(undefined);
    expect(state.messages.m2.outcome).toEqual({ status: "done", summary: "hello.py written and tested.", reason: undefined });
    const note = state.messages.m2.segments.find((s) => s.kind === "note") as { text: string };
    expect(note.text).toBe("Not done yet: no tests yet. Glados continues.");

    const stopped = run([
      { type: "agent_start", ...base, agent: { id: "echo", name: "Echo" } },
      { type: "run_outcome", request_id: "r1", status: "stopped", reason: "it reached the limit of 4 rounds" },
    ]);
    expect(stopped.messages.m1.outcome).toEqual({ status: "stopped", summary: undefined, reason: "it reached the limit of 4 rounds" });
  });
});
