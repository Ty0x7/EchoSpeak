import { describe, expect, it } from "vitest";
import { describeLive } from "./LiveStatus";
import { emptyLive, leanReducer, messageFromTimeline } from "./liveReducer";
import { groupSteps, phaseOf, stepsSummary, type ToolSeg } from "./steps";
import type { LeanEvent, LeanMessageData } from "./types";

const run = (events: LeanEvent[]) => events.reduce(leanReducer, emptyLive("r1"));
const ev = (rest: Record<string, any>): LeanEvent => ({ request_id: "r1", message_id: "m1", agent_id: "echo", ...rest }) as LeanEvent;
const tool = (name: string, status: ToolSeg["status"] = "done", meta?: Record<string, number>): ToolSeg => ({
  kind: "tool", step: 1, id: `${name}-${Math.random()}`, name, label: name, status, output: "", startedAt: 0, meta,
});

describe("live steps", () => {
  it("shows a tool's live line, then its past-tense label and summary", () => {
    let state = run([
      ev({ type: "agent_start", agent: { id: "echo", name: "Echo" } }),
      ev({ type: "tool_start", step: 1, id: "t1", name: "web_search", label: "Searching “budget mic”" }),
      ev({ type: "tool_progress", step: 1, id: "t1", text: "Reading rtings.com (1 of 2)" }),
    ]);
    const running = state.messages.m1.segments[0] as ToolSeg;
    expect(running.detail).toBe("Reading rtings.com (1 of 2)");
    expect(describeLive(state).activity).toBe("Searching “budget mic” · Reading rtings.com (1 of 2)");
    state = leanReducer(state, ev({ type: "tool_end", step: 1, id: "t1", ok: true, output: "…", done_label: "Searched “budget mic”", summary: "14 results · read 2 pages", meta: { results: 14, pages: 2, junk: "x" } }));
    const done = state.messages.m1.segments[0] as ToolSeg;
    expect([done.detail, done.doneLabel, done.summary]).toEqual([undefined, "Searched “budget mic”", "14 results · read 2 pages"]);
    expect(done.meta).toEqual({ results: 14, pages: 2 });
    expect(phaseOf(state.messages.m1)).toBe("Looking over the results…");
  });

  it("restores labels and summaries from saved history", () => {
    const msg = messageFromTimeline({
      messageId: "m1", agentId: "echo", agentName: "Echo", text: "Done.", at: 1,
      timeline: [{ kind: "tool", id: "t1", name: "terminal", label: "Running `pytest`", status: "done", done_label: "Ran `pytest`", summary: "Tests: 12 passed", at: 1 }],
    });
    const seg = msg.segments[0] as ToolSeg;
    expect([seg.doneLabel, seg.summary]).toEqual(["Ran `pytest`", "Tests: 12 passed"]);
  });

  it("folds runs of steps into one readable line", () => {
    expect(stepsSummary([tool("web_search", "done", { results: 9, pages: 2 }), tool("web_search"), tool("web_search"), tool("safe_web_fetch"), tool("safe_web_fetch"), tool("safe_web_fetch"), tool("product_search")]))
      .toBe("Searched the web ×3 · Read 5 pages · Checked prices");
    expect(stepsSummary([tool("file_read"), tool("file_edit"), tool("terminal", "failed")])).toBe("Read 1 file · Changed 1 file · Ran 1 command · 1 failed");
    expect(stepsSummary([tool("web_search", "running")])).toBe("");
  });

  it("groups consecutive tools and keeps text between them", () => {
    const items = groupSteps([
      { kind: "text", step: 1, text: "I'll compare three mics." },
      tool("web_search"), tool("safe_web_fetch"),
      { kind: "text", step: 2, text: "  " },
      tool("product_search"),
      { kind: "text", step: 3, text: "Here they are." },
    ]);
    expect(items.map((i) => (i.kind === "steps" ? `steps:${i.tools.length}` : i.seg.kind))).toEqual(["text", "steps:3", "text"]);
  });

  it("says what is happening between steps", () => {
    const msg = (segments: LeanMessageData["segments"]): LeanMessageData => ({
      messageId: "m", agent: { id: "echo", name: "Echo" }, segments, status: "streaming", text: "", startedAt: 0,
    });
    expect(phaseOf(msg([]))).toBe("Getting started…");
    expect(phaseOf(msg([{ kind: "thinking", step: 1, text: "", startedAt: 0 }]))).toBe("Thinking it through…");
    expect(phaseOf(msg([tool("terminal")]))).toBe("Checking the output…");
    expect(phaseOf(msg([tool("terminal", "failed")]))).toBe("Working out what went wrong…");
    expect(phaseOf(msg([tool("file_write")]))).toBe("Checking the changes…");
    expect(phaseOf(msg([tool("web_search", "running")]))).toBe("");
    expect(phaseOf(msg([{ kind: "text", step: 1, text: "Here" }]))).toBe("");
    expect(phaseOf({ ...msg([tool("terminal")]), status: "done" })).toBe("");
  });
});
