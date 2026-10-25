import React, { useId, useRef, useState } from "react";
import { cloudProviders, fallbackProviders } from "../app/runtime";
import type { ProviderInfo } from "../app/types";
import { ActionIcon } from "./MessageActions";
import { WorkspacePopover } from "../components/WorkspacePopover";

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
  wakeWordEnabled: boolean; toggleWakeWord(): void; onPermissions?(): void; onProviderSettings?(): void;
};
const EFFORTS: ReasoningEffort[] = ["minimal", "low", "medium", "high", "extra_high", "max", "ultra"];
const effortLabel = (effort: string) => effort.replace(/_/g, " ").replace(/\b\w/g, letter => letter.toUpperCase());
const FAVORITES = "echospeak.favoriteModels";
const Chevron = () => <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="m6 9 6 6 6-6" /></svg>;

export function ComposerToolbar(p: ComposerToolbarProps) {
  const [open, setOpen] = useState<"model" | "effort" | "options" | null>(null);
  const modelButton = useRef<HTMLButtonElement>(null);
  const effortButton = useRef<HTMLButtonElement>(null);
  const optionsButton = useRef<HTMLButtonElement>(null);
  const [search, setSearch] = useState("");
  const [onlyFavorites, setOnlyFavorites] = useState(false);
  const [active, setActive] = useState(0);
  const [favorites, setFavorites] = useState<string[]>(() => {
    try { const saved = JSON.parse(localStorage.getItem(FAVORITES) || "[]"); return Array.isArray(saved) ? saved.filter(v => typeof v === "string").slice(0,100) : []; }
    catch { return []; }
  });
  const listId = useId();
  const thinking = p.providerInfo?.model_profile?.thinking_controls;
  const catalog = p.providerInfo?.available_providers?.length ? p.providerInfo.available_providers : fallbackProviders;
  const providers = catalog.filter(provider => provider.id !== "llama_cpp" || provider.id === p.providerDraft.provider);
  const provider = providers.find(row => row.id === p.providerDraft.provider);
  const modelName = p.providerInfo?.model || p.providerDraft.model || "";
  const favoriteKey = (model: string) => p.providerDraft.provider + ":" + model;
  const models = p.modelPickerOptions.filter(model => model.toLowerCase().includes(search.toLowerCase()) && (!onlyFavorites || favorites.includes(favoriteKey(model))));
  const selectedIndex = Math.min(active, Math.max(0, models.length - 1));
  const choose = (model: string) => { p.setProviderDraft(old => ({ ...old, model })); setOpen(null); modelButton.current?.focus(); };
  const star = (model: string) => {
    const key = favoriteKey(model);
    const next = favorites.includes(key) ? favorites.filter(row => row !== key) : [...favorites, key].slice(-100);
    setFavorites(next); try { localStorage.setItem(FAVORITES, JSON.stringify(next)); } catch { /* Session-only preferences. */ }
  };
  return <div className="composer-tools-shell">
    <div className="composer-tools" role="group" aria-label="Chat controls">
      <button ref={modelButton} type="button" className="composer-tool composer-model-trigger" aria-label="Model and chat options" aria-haspopup="dialog" aria-expanded={open === "model"}
        title={modelName || "Choose a model"} disabled={p.switchingProvider} onClick={() => { setSearch(""); setActive(0); setOpen(open === "model" ? null : "model"); }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><path d="m12 3 9 5-9 5-9-5zm-9 9 9 5 9-5M3 16l9 5 9-5" /></svg>
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
    {open === "model" ? <WorkspacePopover anchor={modelButton} label="Choose AI model" width={370} onClose={() => setOpen(null)} className="es-model-popover">
      <div className="es-model-browser">
        <nav className="es-provider-rail" aria-label="Model providers">
          <button type="button" aria-label="Favorite models" aria-pressed={onlyFavorites} onClick={() => { setOnlyFavorites(value => !value); setActive(0); }}>★</button>
          {providers.map(row => <button type="button" key={row.id} aria-label={row.name} aria-pressed={!onlyFavorites && row.id === p.providerDraft.provider}
            disabled={p.switchingProvider || (p.lmStudioOnly && row.id !== p.providerDraft.provider)} title={row.name}
            onClick={() => { setOnlyFavorites(false); setSearch(""); setActive(0); if (row.id !== p.providerDraft.provider) { p.setProviderDraft(old => ({...old,provider:row.id,model:""})); p.setProviderModels([]); } }}>
            {row.name.split(/\s+/).map(word => word[0]).join("").slice(0,2)}
          </button>)}
        </nav>
        <div className="es-model-results">
          <div className="es-model-search"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><circle cx="10" cy="10" r="6" /><path d="m15 15 5 5" /></svg>
            <input autoFocus role="combobox" aria-label="Search models" aria-controls={listId} aria-expanded={true} aria-haspopup="grid" aria-autocomplete="list"
              aria-activedescendant={models.length ? listId + "-" + selectedIndex : undefined} placeholder="Search models…"
              value={search} onChange={event => { setSearch(event.target.value); setActive(0); }}
              onKeyDown={event => {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); const next = (selectedIndex + (event.key === "ArrowDown" ? 1 : -1) + models.length) % Math.max(1,models.length); setActive(next); document.getElementById(listId + "-" + next)?.scrollIntoView({block:"nearest"}); }
                if (event.key === "Enter" && models[selectedIndex]) { event.preventDefault(); choose(models[selectedIndex]); }
              }} />
          </div>
          <div className="es-model-list" role="grid" id={listId} aria-label={onlyFavorites ? "Favorite models" : (provider?.name || "Available models")}>
            {p.modelsLoading ? <p role="status">Loading models…</p> : models.length ? models.map((model,index) => <div key={model} id={listId + "-" + index}
              className={`es-model-row${index === selectedIndex ? " is-highlighted" : ""}`} role="row" aria-selected={model === p.modelPickerValue} onPointerMove={() => setActive(index)}>
              <div role="gridcell"><button type="button" className="es-model-choice" disabled={p.switchingProvider} onClick={() => choose(model)}><strong>{model}</strong><small>{provider?.name || p.providerDraft.provider}{model === p.modelPickerValue ? " · Current" : ""}</small></button></div>
              <div role="gridcell"><button type="button" className="es-model-star" aria-label={`Favorite ${model}`} aria-pressed={favorites.includes(favoriteKey(model))} onClick={() => star(model)}>{favorites.includes(favoriteKey(model)) ? "★" : "☆"}</button></div>
            </div>) : <p role="status">{onlyFavorites ? "No favorite models for this provider." : search ? "No matching models." : cloudProviders.includes(p.providerDraft.provider) ? "Configure this provider in Settings to load its models." : "No models reported by this provider."}</p>}
          </div>
        </div>
      </div>
      <footer><span>{provider?.name || "Native model settings"}</span>{p.onProviderSettings ? <button type="button" onClick={() => { setOpen(null); p.onProviderSettings?.(); }}>Configure providers ↗</button> : null}</footer>
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
