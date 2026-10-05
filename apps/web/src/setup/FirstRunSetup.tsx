import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ModelsSection, SearchSection, TerminalSection, VoiceSection } from "../settings/SettingsPanel";
import { useSettings } from "../settings/useSettings";
import { GenerationSettings } from "../creations/GenerationSettings";
import { creationRequest } from "../creations/api";
import "../creations/creations.css";

const steps = ["Welcome", "AI model", "Web research", "Building apps", "Voice & creations", "Ready"];
export function FirstRunSetup({ apiBase, autoShow = true }: { apiBase: string; autoShow?: boolean }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const settings = useSettings(apiBase);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let disposed = false;
    void creationRequest(apiBase, "/onboarding").then(s => { if (!disposed) { setStep(s.step); setOpen(autoShow && s.show); } }).catch(() => {});
    const show = () => { setOpen(true); setStep(0); };
    window.addEventListener("echospeak:open-setup", show);
    return () => { disposed = true; window.removeEventListener("echospeak:open-setup", show); };
  }, [apiBase]);
  useEffect(() => { if (open) panel.current?.focus(); }, [open]);
  const advance = async (next: number, status = "in_progress") => {
    setSaving(true); setError("");
    try { await creationRequest(apiBase, "/onboarding", "PUT", { step: next, status }); setStep(next); if (status !== "in_progress") setOpen(false); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setSaving(false); }
  };
  if (!open) return null;
  const s = settings.settings;
  return createPortal(<div className="setup-backdrop"><div className="setup-panel" role="dialog" aria-modal="true" aria-labelledby="setup-title" tabIndex={-1} ref={panel} onKeyDown={e => {
    if (e.key !== "Tab") return;
    const elements = panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]');
    if (!elements?.length) return;
    const first = elements[0], last = elements[elements.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }}>
    <header><h2 id="setup-title">Set up EchoSpeak</h2><button className="es-btn es-btn-sm" disabled={saving} onClick={() => void advance(step, "skipped")}>Set up later</button></header>
    <nav aria-label="Setup steps">{steps.map((label, index) => <button key={label} aria-current={step === index ? "step" : undefined} disabled={saving || settings.saveState === "saving"} onClick={() => void advance(index)}>{index + 1}. {label}</button>)}</nav>
    <main>
      {step === 0 ? <><h3>Make Echo ready for you</h3><p>First choose an AI model. Then check search and the optional tools you want to use. Your choices use the same settings you can change later.</p><p>Cloud services need your own account and may charge for use. Local models run on your computer. Downloads and installations only start when you choose them.</p></> : !s ? <p>{settings.error || "Loading your settings…"}</p> : step === 1 ? <ModelsSection s={s} save={settings.save} apiBase={apiBase} /> : step === 2 ? <><p>DuckDuckGo works without an API key. For another index, configure Brave, Tavily, or your SearXNG server. Echo can open sources, read PDFs, and keep a seven-day research notebook within each chat.</p><SearchSection s={s} save={settings.save} /></> : step === 3 ? <><p>Optional: set up the existing Docker sandbox to let Echo run commands and build software. Files and permissions still follow your project and tool settings.</p><TerminalSection s={s} save={settings.save} apiBase={apiBase} /></> : step === 4 ? <><VoiceSection s={s} save={settings.save} apiBase={apiBase} openAdvanced={() => setError("Advanced voice settings are available in Settings after setup.")} /><GenerationSettings s={s} save={settings.save} apiBase={apiBase} /></> : <><h3>Start a conversation</h3><p>Use the model connection test on the AI model step before chatting. Optional capabilities can be configured whenever you need them.</p><ul><li>Ask a question or ask Echo to research a topic.</li><li>Attach a Project folder when you want Echo to build something.</li><li>Enable a creation provider, then ask Echo for an image or video.</li></ul><p>Reopen this checklist from Settings → General → Setup.</p></>}
      {(error || settings.saveError) && <p role="alert">{error || settings.saveError}</p>}
      {settings.saveState === "saving" && <p role="status">Saving settings…</p>}
    </main>
    <footer><button className="es-btn" disabled={step === 0 || saving} onClick={() => void advance(step - 1)}>Back</button><button className="es-btn" disabled={saving || settings.saveState === "saving" || settings.saveState === "error"} onClick={() => void advance(Math.min(5, step + 1), step === 5 ? "completed" : "in_progress")}>{step === 5 ? "Start using EchoSpeak" : "Continue"}</button></footer>
  </div></div>, document.body);
}
