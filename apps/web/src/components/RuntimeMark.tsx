import React from "react";

/**
 * Runtime marks. Codex and Claude Code are named in plain text, which both vendors allow; their logos
 * aren't used as icons (Anthropic: any other use of its names or logos needs written permission), so
 * external runtimes get neutral monograms.
 */
export function RuntimeMark({ runtime = "echo" }: { runtime?: string }) {
  if (runtime === "codex" || runtime === "claude_cli") return <span className={`runtime-mark runtime-mark-${runtime} runtime-monogram`} aria-hidden>{runtime === "codex" ? "Cx" : "CC"}</span>;
  if (runtime === "acp") return <svg className="runtime-mark" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><path d="m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16" /></svg>;
  const src = "/logo.png";
  return <img className={`runtime-mark runtime-mark-${runtime}`} src={src} width="17" height="17" style={{ width:17, height:17, flexShrink:0, objectFit:"contain" }} alt="" aria-hidden draggable={false} />;
}
