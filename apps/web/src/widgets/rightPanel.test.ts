import { describe, expect, it } from "vitest";
import { CHAT_MIN, RIGHT_PANEL_MIN, activityKind, clampPanelWidth, collectActivity } from "./RightPanel";

describe("right panel", () => {
  it("keeps the chat at least CHAT_MIN wide and the panel at least its minimum", () => {
    expect(clampPanelWidth(900, 1000)).toBe(1000 - CHAT_MIN);
    expect(clampPanelWidth(100, 1000)).toBe(RIGHT_PANEL_MIN);
    expect(clampPanelWidth(500, 500)).toBe(RIGHT_PANEL_MIN); // tiny window: panel minimum still wins
  });

  it("collects tool runs from history and the live reply, once each, in order", () => {
    const msg = (id: string, segments: any[]) => ({ messageId: id, agent: { id: "echo", name: "Echo" }, segments, status: "done", text: "", startedAt: 0 }) as any;
    const items = collectActivity([
      msg("a", [{ kind: "tool", id: "t2", name: "terminal", label: "npm test", status: "done", output: "ok", startedAt: 20 }]),
      msg("b", [{ kind: "text", text: "hi" }, { kind: "tool", id: "t1", name: "web_search", label: "Searching", status: "running", output: "", startedAt: 10 }]),
      msg("c", [{ kind: "tool", id: "t2", name: "terminal", label: "npm test", status: "done", output: "ok", startedAt: 20 }]),
      undefined,
    ]);
    expect(items.map((i) => i.id)).toEqual(["t1", "t2"]);
    expect(activityKind("terminal")).toBe("terminal");
    expect(activityKind("file_edit")).toBe("files");
    expect(activityKind("product_search")).toBe("web");
    expect(activityKind("memory_save")).toBe("other");
  });
});
