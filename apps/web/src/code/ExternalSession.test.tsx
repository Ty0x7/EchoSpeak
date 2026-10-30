import { describe, expect, it, vi } from "vitest";
import { eventText, trustedFor } from "./ExternalSession";

describe("external harness event boundary", () => {
  it("remembers trust for the chosen workspace mode without reusing old worktree-only consent", () => {
    const values = new Map([["echospeak.code.trusted.project.codex", "1"], ["echospeak.code.trusted.v2.project.codex.worktree", "1"]]);
    vi.stubGlobal("localStorage", { getItem: (key: string) => values.get(key) || null });
    try {
      expect(trustedFor("project", "codex")).toBe(false);
      expect(trustedFor("project", "codex", "worktree")).toBe(true);
      values.set("echospeak.code.trusted.v2.project.codex.folder", "1");
      expect(trustedFor("project", "codex")).toBe(true);
      expect(trustedFor("another-project", "codex")).toBe(false);
      expect(trustedFor("project", "claude_cli")).toBe(false);
    } finally { vi.unstubAllGlobals(); }
  });
  it("uses the original Codex, Claude Code and ACP text deltas", () => {
    expect(eventText({ seq: 1, kind: "item/agentMessage/delta", data: { delta: "Codex" } })).toBe("Codex");
    expect(eventText({ seq: 2, kind: "claude/text", data: { text: "Claude" } })).toBe("Claude");
    expect(eventText({ seq: 3, kind: "session/update", data: { update: { sessionUpdate: "agent_message_chunk", content: { text: "ACP" } } } })).toBe("ACP");
  });
  it("does not render events of the removed API-key runtime", () => {
    expect(eventText({ seq: 1, kind: "sdk/event", data: { kind: "StreamEvent", data: { event: { delta: { text: "Old" } } } } })).toBe("");
  });
  it("does not turn unknown protocol or auth messages into visible text", () => {
    expect(eventText({ seq: 1, kind: "account/updated", data: { secret: "must not render" } })).toBe("");
  });
});
