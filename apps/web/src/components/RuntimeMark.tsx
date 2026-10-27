import React from "react";

/** Local brand assets; runtime availability is always stated separately. */
export function RuntimeMark({ runtime = "echo" }: { runtime?: string }) {
  if (runtime === "acp") return <svg className="runtime-mark" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><path d="m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16" /></svg>;
  const src = runtime === "codex" ? "/runtime-marks/openai.svg" : runtime === "claude_code" ? "/runtime-marks/claude.svg" : "/logo.png";
  return <img className={`runtime-mark runtime-mark-${runtime}`} src={src} width="17" height="17" style={{ width:17, height:17, flexShrink:0, objectFit:"contain" }} alt="" aria-hidden draggable={false} />;
}
