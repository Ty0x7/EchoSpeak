import React, { useEffect, useRef, useState } from "react";
import { cloudProviders, fallbackProviders } from "../app/runtime";
import type { ProviderInfo } from "../app/types";
import { ActionIcon } from "./MessageActions";

export type ReasoningEffort = "minimal" | "low" | "medium" | "high" | "extra_high" | "max" | "ultra";
type ProviderDraft = { provider: string; model: string; base_url: string };
type ComposerToolbarProps = {
  listening: boolean; voicePhase: string; voiceNotice: string; voiceInputLevel: number;
  startMic(): void; stopMic(): void; monitoring: boolean; toggleMonitor(): void;
  speechEnabled: boolean; setSpeechEnabled(value: boolean): void;
  providerDraft: ProviderDraft; setProviderDraft: React.Dispatch<React.SetStateAction<ProviderDraft>>;
  setProviderModels(models: string[]): void; switchingProvider: boolean; lmStudioOnly: boolean;
  providerInfo: ProviderInfo | null; modelPickerValue: string; modelPickerOptions: string[];
  showModelPicker: boolean; modelsLoading?: boolean; reasoningEffort: ReasoningEffort; setReasoningEffort(value: ReasoningEffort): void;
  thinkingEnabled: boolean; setThinkingEnabled(value: boolean): void;
  voiceReadAloud: boolean; toggleReadAloud(): void; voiceConversationMode: boolean; toggleVoiceMode(): void;
  wakeWordEnabled: boolean; toggleWakeWord(): void;
};

/** Thinking, dictation, voice and a ··· menu (model, provider, preferences), shown inside the input card. */
export function ComposerToolbar(p: ComposerToolbarProps) {
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const thinking = p.providerInfo?.model_profile?.thinking_controls;
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!menu.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", outside); window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", outside); window.removeEventListener("keydown", escape); };
  }, [open]);
  const catalog = p.providerInfo?.available_providers?.length ? p.providerInfo.available_providers : fallbackProviders;
  // llama.cpp has no chat endpoint in the agent runtime; only show it if a chat is already set to it.
  const providers = catalog.filter(provider => provider.id !== "llama_cpp" || provider.id === p.providerDraft.provider);
  const localProviders = providers.filter(provider => provider.local);
  const onlineProviders = providers.filter(provider => !provider.local);
  const isCloud = cloudProviders.includes(p.providerDraft.provider);
  const modelPlaceholder = p.modelsLoading ? "Loading models…" : isCloud ? "Add an API key in Settings" : "No models found";
  const modelName = p.providerInfo?.model || p.providerDraft.model || "";
  return <div className="composer-tools-shell">
    <div className="composer-tools" role="group" aria-label="Chat controls">
      <button type="button" className={`composer-tool ${thinking?.supported && (p.thinkingEnabled || !thinking.toggle) ? "is-on" : ""}`} aria-label="Toggle thinking" aria-pressed={Boolean(thinking?.supported && (p.thinkingEnabled || !thinking.toggle))} title={thinking?.reason || "Toggle thinking"} disabled={!thinking?.toggle || p.switchingProvider} onClick={() => p.setThinkingEnabled(!p.thinkingEnabled)}><ActionIcon name="think" /></button>
      {!p.voiceConversationMode ? <button type="button" className={`composer-tool ${p.listening ? "is-on" : ""}`} aria-label={p.listening ? "Finish dictation" : "Dictate a message"} title={p.listening ? "Finish dictation (Ctrl+M)" : "Dictate a message (Ctrl+M)"} disabled={p.voicePhase === "transcribing"} onClick={p.listening ? p.stopMic : p.startMic}><ActionIcon name="mic" /></button> : null}
      <button type="button" className={`composer-tool composer-voice ${p.voiceConversationMode ? "is-on" : ""}`} aria-label="Voice conversation" aria-pressed={p.voiceConversationMode} onClick={p.toggleVoiceMode} title="Talk with Echo using your selected model"><ActionIcon name="voice" /><span>Voice</span></button>
      <div className="composer-options" ref={menu}>
        <button type="button" className="composer-tool" aria-label="Model and chat options" aria-expanded={open} aria-controls="composer-options-menu" title={modelName ? `Model: ${modelName}` : "Model and chat options"} onClick={() => setOpen(v => !v)}><ActionIcon name="more" /></button>
        {open ? <div id="composer-options-menu" className="composer-options-menu" aria-label="Model and chat options">
          {/* The model choice lives here so it never takes a row under the input. */}
          <div className="composer-options-heading">Model</div>
          <div className="composer-model-strip">
            <select className="composer-provider" aria-label="AI provider" value={p.providerDraft.provider} disabled={p.switchingProvider || p.lmStudioOnly} onChange={e => {
              p.setProviderDraft(old => ({ ...old, provider: e.target.value, model: "" })); p.setProviderModels([]);
            }}>
              {localProviders.length ? <optgroup label="On this PC">{localProviders.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</optgroup> : null}
              {onlineProviders.length ? <optgroup label="Cloud">{onlineProviders.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</optgroup> : null}
            </select>
            {p.showModelPicker || p.modelsLoading ? <select className="composer-model" aria-label="Model" value={p.modelPickerOptions.length ? p.modelPickerValue : ""} disabled={p.switchingProvider || !p.modelPickerOptions.length} title={p.modelPickerValue || modelPlaceholder} onChange={e => p.setProviderDraft(old => ({ ...old, model: e.target.value }))}>
              {p.modelPickerOptions.length ? p.modelPickerOptions.map(model => <option key={model} value={model}>{model}</option>) : <option value="">{modelPlaceholder}</option>}
            </select> : <span className="composer-model-name" title={p.providerInfo?.model}>{p.providerInfo?.model || "Choose a model in Settings"}</span>}
          </div>
          <div className="composer-options-heading">Conversation</div>
          {([
            ["Read replies aloud", p.voiceReadAloud, p.toggleReadAloud],
            ["Sound", p.speechEnabled, () => p.setSpeechEnabled(!p.speechEnabled)],
            ["Wake word", p.wakeWordEnabled, p.toggleWakeWord],
            ["Screen monitor", p.monitoring, p.toggleMonitor],
          ] as [string, boolean, () => void][]).map(([label, on, toggle]) => <button type="button" key={label} aria-pressed={on} onClick={toggle}><span>{label}</span><i data-on={on ? "true" : "false"}>{on ? "On" : "Off"}</i></button>)}
          <label className="composer-effort"><span>Thinking effort</span><select aria-label="Reasoning effort" disabled={!thinking?.effort || p.switchingProvider} title={thinking?.reason} value={p.reasoningEffort} onChange={e => p.setReasoningEffort(e.target.value as ReasoningEffort)}>
            {(["minimal", "low", "medium", "high", "extra_high", "max", "ultra"] as ReasoningEffort[]).map(effort => <option key={effort} value={effort}>{effort.replace(/_/g, " ")}</option>)}
          </select></label>
          {!thinking?.supported ? <small>Thinking controls are unavailable for this model.</small> : !thinking.toggle ? <small>{thinking.reason}</small> : null}
        </div> : null}
      </div>
    </div>
    {!p.voiceConversationMode && (p.voiceNotice || p.listening || p.voicePhase === "transcribing") ? <div className="composer-notice" role="status">{p.voiceNotice || (p.listening ? "Listening · pause to send your message" : "Transcribing your message…")}</div> : null}
  </div>;
}
