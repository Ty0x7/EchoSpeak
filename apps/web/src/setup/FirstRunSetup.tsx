import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ModelsSection, SearchSection, TerminalSection, VoiceSection } from "../settings/SettingsPanel";
import { useSettings } from "../settings/useSettings";
import { GenerationSettings } from "../creations/GenerationSettings";
import { creationRequest } from "../creations/api";
import "../creations/creations.css";
import { EchoFace, echoFaceStyles } from "../components/EchoFace";
import { LocalModelSetup } from "./LocalModelSetup";
import { ThemePicker } from "../theme/ThemePicker";
import { controlDesktopWindow, emitDesktopEvent, isDesktopRuntime, onDesktopEvent, openDesktopSetupWindow } from "../desktop/bridge";
import "./setup.css";

const steps = ["Welcome", "AI model", "Web research", "Building apps", "Voice & creations", "Ready"];
/** A friendly headline for each settings step, and whether it can wait. */
const STEP_INTRO: Record<number, { title: string; lead: string; tag: "Recommended" | "Optional" | "" }> = {
  1: { title: "Pick the brain behind Echo", lead: "Run a model on this PC for privacy, or connect a cloud provider for the strongest answers.", tag: "Recommended" },
  2: { title: "Let Echo look things up", lead: "DuckDuckGo works right away with no key. Add another search provider any time.", tag: "" },
  3: { title: "Give Echo a safe workspace", lead: "A Docker sandbox lets Echo run commands and build software without touching the rest of your PC.", tag: "Optional" },
  4: { title: "Talk and create", lead: "Speak with Echo out loud and make images or videos. Skip this now if you like.", tag: "Optional" },
};
// Sent to every desktop window when setup ends, so the main window can open the first chat.
const SETUP_DONE = "echospeak-setup-done";

/**
 * First-run setup. In a browser it is a dialog over the app. On desktop it has its
 * own window (`windowed`, see SetupWindow): the main window opens that window instead
 * of drawing a dialog, and opens the first chat when the setup window says it's done.
 */
export function FirstRunSetup({ apiBase, autoShow = true, onReady, windowed = false }: { apiBase: string; autoShow?: boolean; onReady?: () => Promise<void> | void; windowed?: boolean }) {
  const [open, setOpen] = useState(windowed);
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
    const load = () => creationRequest(apiBase, "/onboarding").then(s => {
      if (disposed) return;
      if (windowed) { setStep(s.show ? s.step : 0); return; }  // resume a first run; start over when revisiting
      setStep(s.step);
      if (!autoShow || !s.show) return;
      if (isDesktopRuntime()) void openDesktopSetupWindow().catch(() => setOpen(true));
      else setOpen(true);
    }).catch(() => {});
    void load();
    const show = () => { setOpen(true); setStep(0); };
    window.addEventListener("echospeak:open-setup", show);
    // The setup window is hidden, not closed, between uses: refresh when it is shown again.
    const onVisible = () => { if (windowed && document.visibilityState === "visible") { setError(""); void load(); } };
    document.addEventListener("visibilitychange", onVisible);
    return () => { disposed = true; window.removeEventListener("echospeak:open-setup", show); document.removeEventListener("visibilitychange", onVisible); };
  }, [apiBase]);
  // Main window: the setup window finished, so open the first chat here.
  useEffect(() => {
    if (windowed || !autoShow || !isDesktopRuntime()) return;
    let stop: () => void = () => undefined;
    void onDesktopEvent<{ status?: string }>(SETUP_DONE, (payload) => {
      if (payload?.status === "completed") void Promise.resolve(onReady?.()).catch(() => undefined);
    }).then((unlisten) => { stop = unlisten; });
    return () => stop();
  }, [windowed, autoShow]);
  const close = async (status: string) => {
    if (!windowed) { setOpen(false); return; }
    await emitDesktopEvent(SETUP_DONE, { status }).catch(() => undefined);
    await controlDesktopWindow("close").catch(() => undefined);
  };
  useEffect(() => { if (open) panel.current?.focus(); }, [open]);
  const advance = async (next: number, status = "in_progress") => {
    setSaving(true); setError("");
    try { await creationRequest(apiBase, "/onboarding", "PUT", { step: next, status }); setStep(next); if (status !== "in_progress") await close(status); }
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
      if (windowed) { await close("completed"); return; }
      await onReady?.();
      setOpen(false);
      window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]')?.focus(), 80);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };
  if (!open) return null;
  const dialog = <div className={`setup-panel${windowed ? " is-window" : ""}`} role={windowed ? "main" : "dialog"} aria-modal={windowed ? undefined : true} aria-labelledby="setup-title" tabIndex={-1} ref={panel} onKeyDown={e => {
    if (e.key !== "Tab") return;
    const elements = panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]');
    if (!elements?.length) return;
    const first = elements[0], last = elements[elements.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }}>
    <style>{echoFaceStyles}</style><header data-tauri-drag-region>
      <div className="setup-brand-mark" data-tauri-drag-region><span className="setup-mini-face" aria-hidden><i /><i /></span><h2 id="setup-title" data-tauri-drag-region>Set up EchoSpeak</h2></div>
      <nav className="setup-progress" aria-label="Setup steps" data-tauri-drag-region>
        {steps.map((label, index) => <button key={label} type="button" title={label} aria-label={`Step ${index + 1}: ${label}`} aria-current={step === index ? "step" : undefined} data-done={index < step ? "true" : undefined} disabled={saving || checking || settings.saveState === "saving"} onClick={() => void advance(index)}><i /></button>)}
        <span className="setup-progress-label" data-tauri-drag-region>Step {step + 1} of {steps.length} · {steps[step]}</span>
      </nav>
      <button type="button" className="es-btn es-btn-sm es-btn-quiet setup-later" disabled={saving || checking} onClick={() => void advance(step, "skipped")}>Set up later</button>
    </header>
    <main key={step} className="setup-step" data-step={step}>
      {STEP_INTRO[step] ? <div className="setup-intro"><div><h3>{STEP_INTRO[step].title}</h3>{STEP_INTRO[step].tag ? <span className={`setup-tag is-${STEP_INTRO[step].tag.toLowerCase()}`}>{STEP_INTRO[step].tag}</span> : null}</div><p>{STEP_INTRO[step].lead}</p></div> : null}
      {step === 0 ? <div className="setup-welcome"><div className="setup-avatar"><EchoFace size={112} /></div><span className="setup-eyebrow">Your assistant. On your computer.</span><h3>Hey. Let’s get started.</h3><p>Choose a model, make yourself at home, then say hello to Echo. We’ll check that your model can answer before opening your first chat.</p><div className="setup-welcome-points"><span>01 · Pick your model</span><span>02 · Choose your tools</span><span>03 · Start a conversation</span></div><small>Voice, creations and building tools can wait. Nothing downloads until you choose it.</small></div> : !s ? <p>{settings.error || "Loading your settings…"}</p> : step === 1 ? <><ModelsSection s={s} save={settings.save} apiBase={apiBase} /><LocalModelSetup apiBase={apiBase} settings={s} save={settings.save} /></> : step === 2 ? <SearchSection s={s} save={settings.save} /> : step === 3 ? <TerminalSection s={s} save={settings.save} apiBase={apiBase} /> : step === 4 ? <><VoiceSection s={s} save={settings.save} apiBase={apiBase} openAdvanced={() => setError("Advanced voice settings are available in Settings after setup.")} /><GenerationSettings s={s} save={settings.save} apiBase={apiBase} /></> : <div className="setup-ready"><EchoFace size={64} /><h3>Ready to say hello?</h3><p>Check that your selected model can respond. Cloud checks send a small request and may use API credit.</p><button className="es-btn" disabled={checking || saving || settings.saveState === "saving"} onClick={() => void checkResponse()}>{checking ? "Waiting for your model…" : ready ? "Check again" : "Check my model"}</button>{checkMessage && <p role="status" className="setup-check" data-ready={ready}>{checkMessage}</p>}<div className="setup-theme"><h4>How should EchoSpeak look?</h4><ThemePicker label="Choose how EchoSpeak looks" /><small>Change it any time in Settings › General.</small></div><p className="setup-subtle">Your first chat opens after you finish here. You can add optional tools later in Settings.</p></div>}

      {(error || settings.saveError) && <p role="alert">{error || settings.saveError}</p>}
      {settings.saveState === "saving" && <p role="status">Saving settings…</p>}
    </main>
    <footer>
      {step > 0 ? <button type="button" className="es-btn es-btn-quiet" disabled={saving} onClick={() => void advance(step - 1)}>Back</button> : <span />}
      <span className="setup-footer-gap" />
      {STEP_INTRO[step]?.tag === "Optional" ? <button type="button" className="es-btn es-btn-quiet" disabled={saving} onClick={() => void advance(step + 1)}>Skip for now</button> : null}
      <button type="button" className="es-btn es-btn-primary setup-next" disabled={saving || checking || settings.saveState === "saving" || settings.saveState === "error" || (step === 5 && !ready)} onClick={() => void (step === 5 ? finish() : advance(Math.min(5, step + 1)))}>{step === 5 ? "Start chatting" : step === 0 ? "Get started" : "Continue"}</button>
    </footer>
  </div>;
  return windowed ? dialog : createPortal(<div className="setup-backdrop">{dialog}</div>, document.body);
}
