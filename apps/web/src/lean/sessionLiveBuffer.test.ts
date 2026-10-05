import { describe, expect, it } from "vitest";
import { SessionLiveBuffer } from "./sessionLiveBuffer";
const token = (request: string, message: string, data: string) => ({ type: "agent_token", request_id: request, message_id: message, agent_id: "echo", data });
describe("session-owned live turns", () => {
 it("retains hidden tokens and tools while another chat streams", () => {
  const b = new SessionLiveBuffer(); b.start("a", "ra"); b.start("b", "rb");
  b.push("a", { type: "agent_start", request_id: "ra", message_id: "ma", agent: { id: "echo", name: "Echo" } });
  b.push("a", token("ra", "ma", "Before switching. "));
  b.push("a", { type: "tool_start", request_id: "ra", message_id: "ma", id: "t1", name: "web_search" });
  b.push("b", { type: "agent_start", request_id: "rb", message_id: "mb", agent: { id: "echo", name: "Echo" } });
  b.push("b", token("rb", "mb", "Other chat"));
  b.flushAll();
  b.push("a", { type: "tool_end", request_id: "ra", message_id: "ma", id: "t1", ok: true, output: "A source" });
  b.push("a", token("ra", "ma", "While hidden."));
  const a = b.finish("a")!;
  expect(a.messages.ma.segments.filter(s => s.kind === "text").map(s => s.text).join("")).toBe("Before switching. While hidden.");
  const tool = a.messages.ma.segments.find(s => s.kind === "tool");
  expect(tool?.kind === "tool" ? [tool.status, tool.output] : null).toEqual(["done", "A source"]);
  const text = b.get("b")!.messages.mb.segments[0];
  expect(text.kind === "text" ? text.text : null).toBe("Other chat");
 });
 it("does not let superseded requests or one completion clear another chat", () => {
  const b = new SessionLiveBuffer(); b.start("a", "old"); b.push("a", token("old", "old-message", "discard"));
  b.start("a", "new"); b.push("a", token("old", "old-message", "late"));
  b.push("a", { type: "agent_start", request_id: "new", message_id: "new-message", agent: { id: "echo", name: "Echo" } });
  b.push("a", token("new", "new-message", "keep"));
  b.start("b", "rb"); b.finish("b");
  expect(b.finish("a")!.order).toEqual(["new-message"]);
 });
});
