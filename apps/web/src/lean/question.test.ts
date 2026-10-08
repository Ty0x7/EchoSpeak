import { describe, expect, it } from "vitest";
import { describeLive } from "./LiveStatus";
import { emptyLive, leanReducer, messageFromTimeline } from "./liveReducer";
import type { LeanEvent, LeanSegment } from "./types";

const run = (events: LeanEvent[]) => events.reduce(leanReducer, emptyLive("r1"));
const ev = (rest: Record<string, any>): LeanEvent => ({ request_id: "r1", message_id: "m1", agent_id: "echo", ...rest }) as LeanEvent;
type Approval = Extract<LeanSegment, { kind: "approval" }>;

describe("ask_user questions", () => {
  const asked = [
    ev({ type: "agent_start", agent: { id: "echo", name: "Echo" } }),
    ev({ type: "approval_request", step: 1, id: "ask_1", tool: "ask_user", summary: "How should I get the news?", kind: "question",
      question: "How should I get the news?", options: ["Read a news site", "Set up a search key", "Skip it"], allow_other: true }),
  ];

  it("shows the question and its choices while waiting", () => {
    const state = run(asked);
    const seg = state.messages.m1.segments[0] as Approval;
    expect([seg.question, seg.options, seg.allowOther, seg.decision]).toEqual(["How should I get the news?", ["Read a news site", "Set up a search key", "Skip it"], true, ""]);
    expect(describeLive(state).activity).toBe("Echo is asking you something");
    expect(describeLive(state).needsOk).toBe(true);
  });

  it("keeps the answer once given", () => {
    const state = run([...asked, ev({ type: "approval_resolved", id: "ask_1", decision: "answered", answer: "Read a news site" })]);
    const seg = state.messages.m1.segments[0] as Approval;
    expect([seg.decision, seg.answer]).toEqual(["answered", "Read a news site"]);
  });

  it("restores an answered question from history, and plain approvals stay approvals", () => {
    const msg = messageFromTimeline({
      messageId: "m1", agentId: "echo", agentName: "Echo", text: "Okay.", at: 1,
      timeline: [
        { kind: "approval", id: "ask_1", tool: "ask_user", summary: "Which?", question: "Which?", options: ["A", "B"], allow_other: false, decision: "answered", answer: "B", at: 1 },
        { kind: "approval", id: "apr_1", tool: "file_delete", summary: "Deleting x", reason: "deletes", decision: "allow", at: 2 },
      ],
    });
    const [question, approval] = msg.segments as Approval[];
    expect([question.question, question.options, question.answer]).toEqual(["Which?", ["A", "B"], "B"]);
    expect(approval.question).toBe(undefined);
  });
});
