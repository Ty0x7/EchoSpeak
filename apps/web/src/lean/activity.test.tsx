import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { innerScrollerTakes } from "../app/chatFollow";
import { diffStat } from "../workbench/Workbench";
import { AgentsHome } from "./AgentsHome";
import { LeanMessage } from "./LeanMessage";
import { activityDuration, activityHeadline, groupActivity, groupSteps, type ToolSeg } from "./steps";
import type { LeanMessageData, LeanSegment } from "./types";

const tool = (name: string, label: string, startedAt: number, durationMs: number, status: ToolSeg["status"] = "done"): ToolSeg => ({
  kind: "tool", step: 1, id: `${name}-${startedAt}`, name, label, doneLabel: label, status, output: "x", startedAt, durationMs,
});
const thinking = (startedAt: number, endedAt?: number): LeanSegment => ({ kind: "thinking", step: 1, text: "Plan it.", startedAt, endedAt });
const text = (value: string): LeanSegment => ({ kind: "text", step: 2, text: value });
const message = (segments: LeanSegment[], status: LeanMessageData["status"] = "done"): LeanMessageData => ({
  messageId: "m1", agent: { id: "code", name: "qwen3-8b" }, segments, status, startedAt: 0,
} as unknown as LeanMessageData);
const fmt = (ms: number) => `${Math.round(ms / 1000)}s`;

describe("work reads as quiet lines (11.12)", () => {
  it("gathers thinking and steps between parts of the answer into one stretch of work", () => {
    const segments = [thinking(0, 2000), tool("file_read", "Read dates.py", 2000, 500), tool("terminal", "Ran `pytest`", 3000, 4000),
      text("Fixed it."), tool("terminal", "Ran `pytest`", 9000, 1000)];
    const items = groupActivity(groupSteps(segments));
    expect(items.map((item) => item.kind === "activity" ? `activity:${item.parts.length}` : item.kind)).toEqual(["activity:2", "seg", "activity:1"]);
    const first = items[0];
    if (first.kind !== "activity") throw new Error("expected activity");
    expect(activityDuration(first.parts, 99999)).toBe(7000);
    expect(activityHeadline(first.parts, fmt)).toEqual({ title: "Worked for 7s", detail: "Read 1 file · Ran 1 command" });
  });

  it("folds a finished stretch into 'Worked for …' with the steps one click away, and keeps live work open", () => {
    const segments = [thinking(0, 2000), tool("file_read", "Read dates.py", 2000, 500), tool("terminal", "Ran `pytest`", 3000, 4000), text("Fixed it.")];
    const done = renderToStaticMarkup(<LeanMessage data={message(segments)} />);
    expect(done.includes("Worked for 7s")).toBe(true);
    expect(done.includes("Read 1 file · Ran 1 command")).toBe(true);
    expect(done.includes("Read dates.py")).toBe(false);  // folded away until opened
    expect(done.includes("lm-steps-head")).toBe(false);   // no boxed step group while folded
    const live = renderToStaticMarkup(<LeanMessage live data={message([thinking(0, 2000), tool("terminal", "Running `pytest`", 3000, 0, "running")], "streaming")} />);
    expect(live.includes("Running `pytest`")).toBe(true);
    expect(live.includes("lm-shimmer")).toBe(true);
  });

  it("marks the verification note by its status, quietly", () => {
    const html = renderToStaticMarkup(<LeanMessage data={message([text("Done."), { kind: "note", step: 2, text: "Checks pass — test `pytest`: 0 failed, 5 passed, exit 0." }])} />);
    expect(html.includes('data-tone="ok"')).toBe(true);
    const warn = renderToStaticMarkup(<LeanMessage data={message([text("Done."), { kind: "note", step: 2, text: "Not verified: the last check failed." }])} />);
    expect(warn.includes('data-tone="warn"')).toBe(true);
  });
});

describe("following new content", () => {
  const el = (props: Record<string, unknown>, parent: unknown = null) => ({ scrollHeight: 100, clientHeight: 100, scrollTop: 0, parentElement: parent, ...props });

  it("a wheel inside the thinking box scrolls that box, not the chat, so the chat keeps following", () => {
    const g = globalThis as { window?: unknown };
    const saved = g.window;
    g.window = { getComputedStyle: (node: { overflowY?: string }) => ({ overflowY: node.overflowY || "visible" }) };
    try {
      const chat = el({ scrollHeight: 4000, clientHeight: 600, overflowY: "auto" });
      const thought = el({ scrollHeight: 900, clientHeight: 230, scrollTop: 400, overflowY: "auto" }, chat);
      const line = el({}, thought);
      expect(innerScrollerTakes(line as unknown as Element, chat as unknown as Element, -50)).toBe(true);
      // At the top of the thinking box, scrolling up again moves the chat (and stops following, as it should).
      thought.scrollTop = 0;
      expect(innerScrollerTakes(line as unknown as Element, chat as unknown as Element, -50)).toBe(false);
      // Outside any inner scroller, the chat takes the wheel.
      expect(innerScrollerTakes(el({}, chat) as unknown as Element, chat as unknown as Element, -50)).toBe(false);
    } finally {
      g.window = saved;
    }
  });
});

describe("workspace details and agents", () => {
  it("counts added and removed lines in a diff", () => {
    expect(diffStat("--- a/x\n+++ b/x\n@@\n+one\n+two\n-three\n context")).toEqual({ added: 2, removed: 1 });
  });

  it("Agents with nothing open shows a short start screen, not a second roster", () => {
    const html = renderToStaticMarkup(<AgentsHome agents={[{ id: "echo", name: "Echo" } as never, { id: "scout", name: "Jarvis" } as never]} rooms={[]}
      onOpenAgent={() => undefined} onEditAgent={() => undefined} onNewAgent={() => undefined} onOpenRoom={() => undefined} onNewGroup={() => undefined} />);
    expect(html.includes("Create agent")).toBe(true);
    expect(html.includes("Message Echo")).toBe(true);
    expect(html.includes("Jarvis")).toBe(false);
    expect(html.includes("Specialists")).toBe(false);
  });
});
