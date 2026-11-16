import React, { useRef, useState } from "react";
import { WorkspacePopover } from "../components/WorkspacePopover";

/**
 * Reasoning effort for a coding session, using each agent's own control: Echo native's reasoning
 * effort (the same setting as the composer), Codex's per-model efforts (turn/start `effort`) and
 * Claude Code's `--effort` levels. Only the levels the chosen model offers are listed.
 */
export const effortLabel = (effort: string) => ({ xhigh: "Extra high", extra_high: "Extra high", max: "Max", minimal: "Minimal" } as Record<string, string>)[effort]
  || effort.replace(/_/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());

export function CodeEffortPicker({ options, value, defaultValue = "", onChange, disabled, unavailable }: {
  options: string[]; value: string; defaultValue?: string; onChange(value: string): void; disabled?: boolean;
  /** Why there's nothing to choose (the model has no effort control). */
  unavailable?: string;
}) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const shown = value || defaultValue;
  const none = !options.length;
  return <>
    <button ref={button} type="button" className="ec-effort-picker" aria-label="Reasoning effort" aria-haspopup="dialog" aria-expanded={open}
      disabled={disabled || none} title={none ? unavailable || "This model has no effort setting" : "Reasoning effort"} onClick={() => setOpen(v => !v)}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z" />
      </svg>
      <span>{none ? "Default" : shown ? effortLabel(shown) : "Default"}</span>
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="m6 9 6 6 6-6" /></svg>
    </button>
    {open ? <WorkspacePopover anchor={button} label="Reasoning effort" width={190} onClose={() => setOpen(false)}>
      <div className="es-picker-heading">Reasoning effort</div>
      <div role="menu" aria-label="Reasoning effort">{options.map(option => <button key={option} className="es-picker-option" type="button" role="menuitemradio"
        aria-checked={shown === option} onClick={() => { onChange(option); setOpen(false); button.current?.focus(); }}>
        {effortLabel(option)}{option === defaultValue ? <small>Default</small> : null}{shown === option ? <span aria-hidden>✓</span> : null}
      </button>)}</div>
    </WorkspacePopover> : null}
  </>;
}
