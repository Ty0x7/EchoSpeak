import { describe, expect, it } from "vitest";
import { eventText } from "./ExternalSession";

describe("external harness event boundary", () => {
  it("uses the original Codex, SDK and ACP text deltas", () => {
    expect(eventText({ seq: 1, kind: "item/agentMessage/delta", data: { delta: "Codex" } })).toBe("Codex");
    expect(eventText({ seq: 2, kind: "sdk/event", data: { kind: "StreamEvent", data: { event: { delta: { text: "Claude" } } } } })).toBe("Claude");
    expect(eventText({ seq: 3, kind: "session/update", data: { update: { sessionUpdate: "agent_message_chunk", content: { text: "ACP" } } } })).toBe("ACP");
  });
  it("does not repeat Claude's completed assistant message after its partial stream", () => {
    expect(eventText({ seq: 1, kind: "sdk/event", data: { kind: "AssistantMessage", data: { content: "Duplicate" } } })).toBe("");
  });
  it("does not turn unknown protocol or auth messages into visible text", () => {
    expect(eventText({ seq: 1, kind: "account/updated", data: { secret: "must not render" } })).toBe("");
  });
});
