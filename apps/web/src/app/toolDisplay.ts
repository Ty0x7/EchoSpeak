// Moved out of index.tsx (10.0 split). Kept verbatim.
import { type Message, type MessageUsage } from "./types";

export const estimateTokens = (text: string): number =>
  Math.max(0, Math.round(String(text || "").length / 3.5));

/** Tools that fire every turn and would spam the chat activity list. */
export const SILENT_CHAT_TOOLS = new Set([
  "get_system_time",
  "project_update_context",
]);

/** Human label for any tool including MCP (`mcp__server__tool`). */
export const formatToolDisplayName = (rawName: string): string => {
  const name = String(rawName || "").trim();
  if (!name) return "tool";
  if (name.startsWith("mcp__")) {
    const parts = name.split("__").filter(Boolean);
    // mcp, server, tool...
    if (parts.length >= 3) {
      const server = parts[1].replace(/_/g, " ");
      const tool = parts.slice(2).join("__").replace(/_/g, " ");
      return `MCP · ${server} · ${tool}`;
    }
    return `MCP · ${name.replace(/^mcp__/, "").replace(/__/g, " · ").replace(/_/g, " ")}`;
  }
  const known: Record<string, string> = {
    web_search: "Web search",
    sports_live: "Live sports",
    file_read: "File read",
    file_write: "File write",
    file_list: "File list",
    file_delete: "File delete",
    file_move: "File move",
    file_copy: "File copy",
    file_mkdir: "Make folder",
    terminal_run: "Terminal",
    artifact_write: "Artifact write",
    notepad_write: "Notepad",
    browse_task: "Browse page",
    youtube_transcript: "YouTube transcript",
    vision_qa: "Vision",
    analyze_screen: "Screen OCR",
    take_screenshot: "Screenshot",
    calculate: "Calculate",
    system_info: "System info",
    daily_briefing: "Daily briefing",
    discord_read_channel: "Discord read",
    discord_send_channel: "Discord send",
  };
  if (known[name]) return known[name];
  return name.replace(/_/g, " ");
};

/** Pull a short human preview from tool input (JSON / query= / free text). */
export const previewToolInput = (rawName: string, rawInput: string, maxLen = 100): string => {
  let inputPreview = String(rawInput || "").replace(/\s+/g, " ").trim();
  if (!inputPreview) return "";
  const m =
    inputPreview.match(/['"]query['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]path['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]command['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]url['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]channel['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]text['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]input['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]name['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/\bquery\s*[:=]\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/\bpath\s*[:=]\s*['"]([^'"]+)['"]/i);
  if (m && m[1]) inputPreview = m[1].trim().replace(/['"]$/g, "");
  // Strip dict braces noise
  if (inputPreview.startsWith("{") && inputPreview.length > 80) {
    inputPreview = inputPreview.slice(0, 80);
  }
  if (inputPreview.length > maxLen) inputPreview = inputPreview.slice(0, maxLen) + "…";
  if (/how(?:'re| are) you|look great|liking how you look/i.test(inputPreview) && inputPreview.length > 100) {
    inputPreview = inputPreview.slice(0, 80) + "…";
  }
  return inputPreview;
};

export const toolActivityStepType = (rawName: string): "search" | "read" | "tool" => {
  const n = String(rawName || "").toLowerCase();
  if (n === "web_search" || n === "sports_live") return "search";
  if (n === "file_read" || n === "browse_task" || n === "youtube_transcript") return "read";
  return "tool"; // includes MCP mcp__server__tool
};

/** Running / done labels for any tool (built-in + MCP). */
export const formatToolActivity = (
  rawName: string,
  phase: "start" | "done" | "failed",
  opts?: { input?: string; output?: string; error?: string }
): string => {
  const name = String(rawName || "tool");
  const low = name.toLowerCase();
  const label = formatToolDisplayName(name);
  const input = previewToolInput(name, opts?.input || "");
  const out = String(opts?.output || "").replace(/\s+/g, " ").trim();
  const err = String(opts?.error || "").replace(/\s+/g, " ").trim();

  if (phase === "failed") {
    return `Failed ${label}${err ? `: ${err.slice(0, 100)}` : ""}`;
  }

  // Specialized search copy — one user-facing summary per ToolRun (no provider "via" spam).
  if (low === "web_search") {
    // Wrapper shells are not user-facing completion lines.
    if (/\(expanded to |\(superseded by canonical/i.test(out)) {
      return phase === "start" ? (input ? `Searching: ${input}` : "Searching the web…") : "";
    }
    if (phase === "start") return input ? `Searching: ${input}` : "Searching the web…";
    const insufficient =
      /search_evidence_insufficient|accepted=false/i.test(out) ||
      (/insufficient/i.test(out) && !/\d+\.\s/.test(out));
    const sources = (out.match(/^\s*\d+\.\s+/gm) || []).length;
    if (insufficient) return input ? `Search finished (weak evidence): ${input}` : "Search finished — weak evidence";
    if (sources > 0) return input ? `Search done (${sources} sources): ${input}` : `Search done (${sources} sources)`;
    return input ? `Search done: ${input}` : "Search done";
  }
  if (low === "get_system_time") {
    if (phase === "start") return "Checking the time…";
    return "Got the time";
  }
  if (low === "sports_live") {
    if (phase === "start") return input ? `Live sports: ${input}` : "Live sports data…";
    const ok = /ok\s*=\s*true/i.test(out);
    if (ok) return input ? `Live sports done: ${input}` : "Live sports done";
    return input ? `Live sports unavailable: ${input}` : "Live sports unavailable — may fall back to web";
  }

  if (phase === "start") {
    if (low.startsWith("mcp__")) {
      return input ? `${label}: ${input}` : `Using ${label}…`;
    }
    if (low === "file_read") return input ? `Reading: ${input}` : "Reading file…";
    if (low === "file_write") return input ? `Writing: ${input}` : "Writing file…";
    if (low === "terminal_run") return input ? `Running: ${input}` : "Running terminal…";
    if (low === "browse_task") return input ? `Browsing: ${input}` : "Browsing page…";
    return input ? `${label}: ${input}` : `Using ${label}…`;
  }

  // done
  if (low.startsWith("mcp__")) {
    const preview = out.slice(0, 80);
    return preview ? `${label} done — ${preview}${out.length > 80 ? "…" : ""}` : `${label} done`;
  }
  if (low === "file_read") return input ? `Read ${input}` : "File read done";
  if (low === "file_write") return input ? `Wrote ${input}` : "File write done";
  if (low === "terminal_run") {
    const code = out.match(/ExitCode\s*=\s*(-?\d+)/i)?.[1];
    return code != null ? `Terminal done (exit ${code})` : "Terminal done";
  }
  const preview = out.slice(0, 90);
  return preview ? `${label} done — ${preview}${out.length > 90 ? "…" : ""}` : `${label} done`;
};

export const formatTokenCount = (n: number): string => {
  if (!Number.isFinite(n) || n < 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(Math.round(n));
};

export const buildMessageUsage = (
  text: string,
  priorMessages: Message[],
  contextWindow: number,
  meta?: { provider?: string; model?: string }
): MessageUsage => {
  const tokens = estimateTokens(text);
  const prior = priorMessages.reduce((sum, m) => sum + (m.usage?.tokens ?? estimateTokens(m.text)), 0);
  const window = contextWindow > 0 ? contextWindow : 32768;
  return {
    tokens,
    contextUsed: prior + tokens,
    contextWindow: window,
    provider: meta?.provider,
    model: meta?.model,
  };
};

