import React, { useRef, useState } from "react";
import { ConnectedModelBrowser } from "./ConnectedModelBrowser";
import { RuntimeMark } from "../components/RuntimeMark";
import type { ProviderInfo } from "../app/types";
import { ActionIcon } from "./MessageActions";
import { WorkspacePopover } from "../components/WorkspacePopover";

export type ReasoningEffort = "minimal" | "low" | "medium" | "high" | "extra_high" | "max" | "ultra";
type ProviderDraft = { provider: string; model: string; base_url: string };
type ComposerToolbarProps = {
  apiBase: string;
  listening: boolean; voicePhase: string; voiceNotice: string; voiceInputLevel: number;
  startMic(): void; stopMic(): void; monitoring: boolean; toggleMonitor(): void;
  speechEnabled: boolean; setSpeechEnabled(value: boolean): void;
  providerDraft: ProviderDraft; setProviderDraft: React.Dispatch<React.SetStateAction<ProviderDraft>>;
  setProviderModels(models: string[]): void; switchingProvider: boolean; lmStudioOnly: boolean;
  providerInfo: ProviderInfo | null; modelPickerValue: string; modelPickerOptions: string[];
  showModelPicker: boolean; modelsLoading?: boolean; reasoningEffort: ReasoningEffort; setReasoningEffort(value: ReasoningEffort): void;
  thinkingEnabled: boolean; setThinkingEnabled(value: boolean): void;
  voiceReadAloud: boolean; toggleReadAloud(): void; voiceConversationMode: boolean; toggleVoiceMode(): void;
  wakeWordEnabled: boolean; toggleWakeWord(): void; onPermissions?(): void; onProviderSettings?(): void;
};
const EFFORTS: ReasoningEffort[] = ["minimal", "low", "medium", "high", "extra_high", "max", "ultra"];
const effortLabel = (effort: string) => effort.replace(/_/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());
const Chevron = () => <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="m6 9 6 6 6-6" /></svg>;

export function ComposerToolbar(p: ComposerToolbarProps) {
  const [open, setOpen] = useState<"model" | "effort" | "options" | null>(null);
  const modelButton = useRef<HTMLButtonElement>(null);
  const effortButton = useRef<HTMLButtonElement>(null);
  const optionsButton = useRef<HTMLButtonElement>(null);
  const thinking = p.providerInfo?.model_profile?.thinking_controls;
  const modelName = p.providerInfo?.model || p.providerDraft.model || "";
  return <div className="composer-tools-shell">
    <div className="composer-tools" role="group" aria-label="Chat controls">
      <button ref={modelButton} type="button" className="composer-tool composer-model-trigger" aria-label="Model and chat options" aria-haspopup="dialog" aria-expanded={open === "model"}
        title={modelName || "Choose a model"} disabled={p.switchingProvider} onClick={() => setOpen(open === "model" ? null : "model")}>
        <RuntimeMark runtime={p.providerDraft.provider === "openai" ? "codex" : p.providerDraft.provider === "anthropic" ? "claude_cli" : "echo"} />
        <span>{modelName || "Choose model"}</span><Chevron />
      </button>
      <button ref={effortButton} type="button" className="composer-tool composer-effort-trigger" aria-label="Reasoning effort" aria-haspopup="dialog" aria-expanded={open === "effort"}
        title={thinking?.effort ? "Reasoning effort" : thinking?.reason || "This model does not offer reasoning controls"}
        disabled={!thinking?.effort || p.switchingProvider} onClick={() => setOpen(open === "effort" ? null : "effort")}><ActionIcon name="think" /><span>{effortLabel(p.reasoningEffort)}</span><Chevron /></button>
      {p.onPermissions ? <button type="button" className="composer-tool composer-permissions" aria-label="Open Echo permissions" onClick={p.onPermissions} title="Echo's current permission settings">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></svg><span>Permissions</span></button> : null}
      {!p.voiceConversationMode ? <button type="button" className={`composer-tool ${p.listening ? "is-on" : ""}`} aria-label={p.listening ? "Finish dictation" : "Dictate a message"} title={p.listening ? "Finish dictation (Ctrl+M)" : "Dictate a message (Ctrl+M)"} disabled={p.voicePhase === "transcribing"} onClick={p.listening ? p.stopMic : p.startMic}><ActionIcon name="mic" /></button> : null}
      <button type="button" className={`composer-tool composer-voice ${p.voiceConversationMode ? "is-on" : ""}`} aria-label="Voice conversation" aria-pressed={p.voiceConversationMode} onClick={p.toggleVoiceMode} title="Talk with Echo using your selected model"><ActionIcon name="voice" /></button>
      <button ref={optionsButton} type="button" className="composer-tool" aria-label="Conversation options" aria-haspopup="dialog" aria-expanded={open === "options"} onClick={() => setOpen(open === "options" ? null : "options")}><ActionIcon name="more" /></button>
    </div>
    {open === "model" ? <WorkspacePopover anchor={modelButton} label="Choose AI model" width={390} onClose={() => setOpen(null)} className="es-model-popover">
      <ConnectedModelBrowser apiBase={p.apiBase} info={p.providerInfo} provider={p.providerDraft.provider} model={p.modelPickerValue} locked={p.lmStudioOnly}
        onChoose={(provider, model, models) => { p.setProviderModels(models); p.setProviderDraft(old => ({ ...old, provider, model })); setOpen(null); modelButton.current?.focus(); }}
        onConfigure={p.onProviderSettings ? () => { setOpen(null); p.onProviderSettings?.(); } : undefined} />
    </WorkspacePopover> : null}
    {open === "effort" ? <WorkspacePopover anchor={effortButton} label="Reasoning" width={174} onClose={() => setOpen(null)}>
      <div className="es-picker-heading">Reasoning</div><div role="menu" aria-label="Reasoning effort">{EFFORTS.map(effort => <button className="es-picker-option" type="button" role="menuitemradio" aria-checked={p.reasoningEffort === effort} key={effort}
        onClick={() => { p.setReasoningEffort(effort); setOpen(null); effortButton.current?.focus(); }}>{effortLabel(effort)}{effort === "medium" ? <small>Default</small> : null}{p.reasoningEffort === effort ? <span aria-hidden>✓</span> : null}</button>)}</div>
    </WorkspacePopover> : null}
    {open === "options" ? <WorkspacePopover anchor={optionsButton} label="Conversation options" width={250} onClose={() => setOpen(null)}>
      <div className="es-picker-heading">Conversation</div>
      <button className="es-picker-option" type="button" disabled={!thinking?.toggle || p.switchingProvider} aria-pressed={Boolean(thinking?.supported && (p.thinkingEnabled || !thinking.toggle))} onClick={() => p.setThinkingEnabled(!p.thinkingEnabled)}><span>Thinking</span><small>{thinking?.supported ? p.thinkingEnabled || !thinking.toggle ? "On" : "Off" : "Unavailable"}</small></button>
      {([
        ["Read replies aloud", p.voiceReadAloud, p.toggleReadAloud], ["Sound", p.speechEnabled, () => p.setSpeechEnabled(!p.speechEnabled)],
        ["Wake word", p.wakeWordEnabled, p.toggleWakeWord], ["Screen monitor", p.monitoring, p.toggleMonitor],
      ] as [string,boolean,()=>void][]).map(([label,on,toggle]) => <button className="es-picker-option" type="button" key={label} aria-pressed={on} onClick={toggle}><span>{label}</span><small>{on ? "On" : "Off"}</small></button>)}
    </WorkspacePopover> : null}
    {!p.voiceConversationMode && (p.voiceNotice || p.listening || p.voicePhase === "transcribing") ? <div className="composer-notice" role="status">{p.voiceNotice || (p.listening ? "Listening · pause to send your message" : "Transcribing your message…")}</div> : null}
  </div>;
}
