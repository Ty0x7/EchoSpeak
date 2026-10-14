import { describe, expect, it } from "vitest";
import { asToolResult, hostInitResult } from "./McpAppView";
import { validateWidget } from "./validate";

const ref = { id: "app-" + "a".repeat(24), session_id: "chat-a", server: "sales", tool: "show_chart", prefers_border: true };

describe("MCP Apps host", () => {
  it("accepts only stored view references, never HTML in the widget", () => {
    expect(validateWidget({ type: "mcp_app", data: ref })).toEqual({ type: "mcp_app", data: ref });
    expect(validateWidget({ type: "mcp_app", data: { ...ref, id: "../../settings" } })).toBeNull();
    expect(validateWidget({ type: "mcp_app", data: { ...ref, session_id: "" } })).toBeNull();
    const withHtml = validateWidget({ type: "mcp_app", data: { ...ref, html: "<script>steal()</script>" } });
    expect(JSON.stringify(withHtml)).not.toContain("steal");
  });

  it("answers ui/initialize with what EchoSpeak actually supports", () => {
    const init = hostInitResult("2026-01-26", "dark");
    expect(init.hostInfo.name).toBe("EchoSpeak");
    expect(Object.keys(init.hostCapabilities).sort()).toEqual(["logging", "openLinks", "serverResources", "serverTools"]);
    expect(init.hostContext.theme).toBe("dark");
    expect(init.hostContext.availableDisplayModes).toEqual(["inline"]);
    expect(hostInitResult("", "light").protocolVersion).toBe("2026-01-26");
  });

  it("sends the tool result as a CallToolResult", () => {
    const full = { content: [{ type: "text", text: "ok" }], structuredContent: { rows: 3 } };
    expect(asToolResult(full)).toBe(full);
    expect(asToolResult("plain text")).toEqual({ content: [{ type: "text", text: "plain text" }] });
  });
});
