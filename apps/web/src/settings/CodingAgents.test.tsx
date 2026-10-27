import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { accountLine, CodingAgents } from "./CodingAgents";
import { eventText } from "../code/ExternalSession";
import { runtimeName } from "../code/model";

describe("coding agents in Settings › Models", () => {
  it("says which plan each agent is signed in with, never more", () => {
    expect(accountLine({ installed: true, signed_in: true, method: "chatgpt", plan: "plus" }, "codex")).toBe("Signed in with ChatGPT · Plus plan");
    expect(accountLine({ installed: true, signed_in: true, method: "claude.ai", plan: "pro" }, "claude_cli")).toBe("Signed in with your Claude Pro plan");
    expect(accountLine({ installed: true, signed_in: true, method: "api_key" }, "claude_cli")).toBe("Signed in (api_key)");
    expect(accountLine({ installed: true, signed_in: false }, "codex")).toBe("Not signed in");
  });

  it("lists Codex and Claude Code with their own sign-in, plus the API-key and ACP options", () => {
    const html = renderToStaticMarkup(<CodingAgents s={{ codex_executable: "" }} save={async () => {}} apiBase="http://localhost" />);
    expect(html.includes("Coding agents")).toBe(true);
    for (const text of ["Codex", "Claude Code", "Claude Agent", "Other ACP agents", "Codex location", "Claude Code location"]) expect(html.includes(text)).toBe(true);
    expect(html.includes("never sees your sign-in")).toBe(true);
  });
});

describe("Claude Code runtime in Echo Code", () => {
  it("has one name everywhere", () => {
    expect(runtimeName("claude_cli")).toBe("Claude Code");
    expect(runtimeName("claude_code")).toBe("Claude Agent");
    expect(runtimeName("codex")).toBe("Codex");
    expect(runtimeName(undefined)).toBe("Echo native");
  });

  it("shows streamed text and tool use as plain text", () => {
    expect(eventText({ seq: 1, kind: "claude/text", data: { text: "Fixed it." } })).toBe("Fixed it.");
    expect(eventText({ seq: 2, kind: "claude/tool", data: { tool: "Bash", detail: "npm test" } })).toBe("\n› Bash npm test\n");
    expect(eventText({ seq: 3, kind: "claude/result", data: { is_error: true } }).includes("failed turn")).toBe(true);
    expect(eventText({ seq: 4, kind: "claude/result", data: { is_error: false } })).toBe("");
  });
});
