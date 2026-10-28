import React from "react";

/** Chat's answerer is the model itself: its maker's mark, or a plain chip for models on this PC. */
export function ModelMark({ provider, size = 26 }: { provider?: string; size?: number }) {
  const src = provider === "openai" ? "/runtime-marks/openai.svg" : provider === "anthropic" ? "/runtime-marks/claude.svg" : "";
  return <span className="lm-avatar lm-model-mark" style={{ width: size, height: size }} aria-hidden data-provider={provider || "local"}>
    {src ? <img src={src} alt="" width={Math.round(size * 0.62)} height={Math.round(size * 0.62)} draggable={false} />
      : <svg width={Math.round(size * 0.6)} height={Math.round(size * 0.6)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <rect x="6" y="6" width="12" height="12" rx="2.5" /><path d="M9.5 2.5v3M14.5 2.5v3M9.5 18.5v3M14.5 18.5v3M2.5 9.5h3M2.5 14.5h3M18.5 9.5h3M18.5 14.5h3" />
        </svg>}
  </span>;
}
