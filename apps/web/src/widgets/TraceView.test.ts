import { describe, expect, it } from "vitest";
import { lastReplyId } from "./RightPanel";
import { traceRows, type TraceRecord } from "./TraceView";

const record: TraceRecord = {
  trace_id: "t".repeat(32), message_id: "msg_aaaaaaaaaaaa", agent: "echo",
  spans: [
    { name: "agent.turn", id: "r", parent: "", start_ms: 0, duration_ms: 4000, status: "ok", attrs: {} },
    { name: "model.call", id: "m1", parent: "r", start_ms: 0, duration_ms: 1000, status: "ok", attrs: { step: 1, model: "qwen3.8", tool_calls: 1, prompt_tokens: 900, completion_tokens: 40 } },
    { name: "approval.wait", id: "w", parent: "r", start_ms: 1000, duration_ms: 2000, status: "ok", attrs: { tool: "computer_action", decision: "allow" } },
    { name: "tool.run", id: "t", parent: "r", start_ms: 3000, duration_ms: 500, status: "error", attrs: { tool: "computer_action", ok: false } },
    { name: "model.call", id: "m2", parent: "r", start_ms: 3500, duration_ms: 500, status: "ok", attrs: { step: 2 } },
  ],
};

describe("reply timing", () => {
  it("puts every span on the reply's time scale and says where the time went", () => {
    const { totalMs, share, rows } = traceRows(record);
    expect(totalMs).toBe(4000);
    expect(rows.map((r) => [r.label, r.offset, r.width])).toEqual([
      ["Thinking · step 1", 0, 25], ["Waiting for you", 25, 50], ["computer_action", 75, 12.5], ["Thinking · step 2", 87.5, 12.5],
    ]);
    expect(share).toEqual({ model: 38, tool: 13, wait: 50, other: 0 });
    expect(rows[0].detail).toBe("qwen3.8 · 1 tool call · 900 in / 40 out");
    expect(rows[2].failed).toBe(true);
  });
  it("explains the newest finished reply, not one still streaming", () => {
    const msg = (messageId: string, status: "done" | "streaming") => ({ messageId, status } as never);
    expect(lastReplyId([msg("msg_111111111111", "done"), msg("msg_222222222222", "streaming")])).toBe("msg_111111111111");
    expect(lastReplyId([msg("local-draft", "done")])).toBe("");
  });
});
