import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ModelsSection, SearchSection, TerminalSection, VoiceSection } from "../settings/SettingsPanel";
import { useSettings } from "../settings/useSettings";
import { GenerationSettings } from "../creations/GenerationSettings";
import { creationRequest } from "../creations/api";
import "../creations/creations.css";
import { EchoFace, echoFaceStyles } from "../components/EchoFace";
import { LocalModelSetup } from "./LocalModelSetup";
import "./setup.css";

const steps = ["Welcome", "AI model", "Web research", "Building apps", "Voice & creations", "Ready"];
export function FirstRunSetup({ apiBase, autoShow = true, onReady }: { apiBase: string; autoShow?: boolean; onReady?: () => Promise<void> | void }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [ready, setReady] = useState(false);
  const [checkMessage, setCheckMessage] = useState("");
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
  const s = settings.settings;
  const selection = JSON.stringify([s?.use_local_models, s?.local, s?.default_cloud_provider, s?.openai, s?.gemini, s?.anthropic, s?.xai]);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  useEffect(() => { setReady(false); setCheckMessage(""); }, [selection, open]);
  const checkResponse = async () => {
    if (!s) return;
    const testedSelection = selection;
    setChecking(true); setReady(false); setError("");
    try {
      const local = s.use_local_models !== false;
      const provider = local ? String(s.local?.provider || "lmstudio") : String(s.default_cloud_provider || "openai");
      const result = await creationRequest(apiBase, "/settings/test", "POST", { target: local ? (provider === "ollama" ? "ollama" : "local") : provider, check: "generation", model: local ? s.local?.model_name : s[provider]?.model, base_url: local ? s.local?.base_url : undefined });
      if (selectionRef.current === testedSelection) { setReady(Boolean(result.ok)); setCheckMessage(String(result.message || "The model could not respond.")); }
    } catch (e) { setCheckMessage(e instanceof Error ? e.message : String(e)); }
    finally { setChecking(false); }
  };
  const finish = async () => {
    if (!ready) return;
    setSaving(true); setError("");
    try {
      await creationRequest(apiBase, "/onboarding", "PUT", { step: 5, status: "completed" });
      await onReady?.();
      setOpen(false);
      window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]')?.focus(), 80);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };
  if (!open) return null;
  return createPortal(<div className="setup-backdrop"><div className="setup-panel" role="dialog" aria-modal="true" aria-labelledby="setup-title" tabIndex={-1} ref={panel} onKeyDown={e => {
    if (e.key !== "Tab") return;
    const elements = panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]');
    if (!elements?.length) return;
    const first = elements[0], last = elements[elements.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }}>
    <style>{echoFaceStyles}</style><header><div><span className="setup-brand">EchoSpeak</span><h2 id="setup-title">{step === 0 ? "Your first few minutes" : steps[step]}</h2></div><button className="es-btn es-btn-sm" disabled={saving || checking} onClick={() => void advance(step, "skipped")}>Set up later</button></header>
    <nav aria-label="Setup steps">{steps.map((label, index) => <button key={label} aria-current={step === index ? "step" : undefined} disabled={saving || checking || settings.saveState === "saving"} onClick={() => void advance(index)}>{index + 1}. {label}</button>)}</nav>
    <main key={step} className="setup-step" data-step={step}>
      {step === 0 ? <div className="setup-welcome"><div className="setup-avatar"><EchoFace size={112} /></div><span className="setup-eyebrow">Your assistant. On your computer.</span><h3>Hey. Let’s get started.</h3><p>Choose a model, make yourself at home, then say hello to Echo. We’ll check that your model can answer before opening your first chat.</p><div className="setup-welcome-points"><span>01 · Pick your model</span><span>02 · Choose your tools</span><span>03 · Start a conversation</span></div><small>Voice, creations and building tools can wait. Nothing downloads until you choose it.</small></div> : !s ? <p>{settings.error || "Loading your settings…"}</p> : step === 1 ? <><ModelsSection s={s} save={settings.save} apiBase={apiBase} /><LocalModelSetup apiBase={apiBase} settings={s} save={settings.save} /></> : step === 2 ? <><p>DuckDuckGo works without an API key. For another index, configure Brave, Tavily, or your SearXNG server. Echo can open sources, read PDFs, and keep a seven-day research notebook within each chat.</p><SearchSection s={s} save={settings.save} /></> : step === 3 ? <><p>Optional: set up the existing Docker sandbox to let Echo run commands and build software. Files and permissions still follow your project and tool settings.</p><TerminalSection s={s} save={settings.save} apiBase={apiBase} /></> : step === 4 ? <><VoiceSection s={s} save={settings.save} apiBase={apiBase} openAdvanced={() => setError("Advanced voice settings are available in Settings after setup.")} /><GenerationSettings s={s} save={settings.save} apiBase={apiBase} /></> : <div className="setup-ready"><EchoFace size={64} /><h3>Ready to say hello?</h3><p>Check that your selected model can respond. Cloud checks send a small request and may use API credit.</p><button className="es-btn" disabled={checking || saving || settings.saveState === "saving"} onClick={() => void checkResponse()}>{checking ? "Waiting for your model…" : ready ? "Check again" : "Check my model"}</button>{checkMessage && <p role="status" className="setup-check" data-ready={ready}>{checkMessage}</p>}<p className="setup-subtle">Your first chat opens after you finish here. You can add optional tools later in Settings.</p></div>}

      {(error || settings.saveError) && <p role="alert">{error || settings.saveError}</p>}
      {settings.saveState === "saving" && <p role="status">Saving settings…</p>}
    </main>
    <footer><button className="es-btn" disabled={step === 0 || saving} onClick={() => void advance(step - 1)}>Back</button><button className="es-btn" disabled={saving || checking || settings.saveState === "saving" || settings.saveState === "error" || (step === 5 && !ready)} onClick={() => void (step === 5 ? finish() : advance(Math.min(5, step + 1)))}>{step === 5 ? "Start chatting" : step === 0 ? "Get started" : "Continue"}</button></footer>
  </div></div>, document.body);
}
