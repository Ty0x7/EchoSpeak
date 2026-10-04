import React, { useCallback, useEffect, useRef, useState } from "react";
import { fallbackProviders, geminiModelOptions, openaiModelOptions } from "../app/runtime";
import type { ProviderInfo } from "../app/types";

export type ReasoningEffort = "minimal" | "low" | "medium" | "high" | "extra_high" | "max" | "ultra";
type ProviderDraft = { provider: string; model: string; base_url: string };

type ComposerToolbarProps = {
  listening: boolean;
  voicePhase: string;
  voiceNotice: string;
  voiceInputLevel: number;
  startMic(): void;
  stopMic(): void;
  monitoring: boolean;
  toggleMonitor(): void;
  speechEnabled: boolean;
  setSpeechEnabled(value: boolean): void;
  providerDraft: ProviderDraft;
  setProviderDraft: React.Dispatch<React.SetStateAction<ProviderDraft>>;
  setProviderModels(models: string[]): void;
  switchingProvider: boolean;
  lmStudioOnly: boolean;
  providerInfo: ProviderInfo | null;
  modelPickerValue: string;
  modelPickerOptions: string[];
  showModelPicker: boolean;
  reasoningEffort: ReasoningEffort;
  setReasoningEffort(value: ReasoningEffort): void;
  thinkingEnabled: boolean;
  setThinkingEnabled(value: boolean): void;
  voiceReadAloud: boolean;
  toggleReadAloud(): void;
  voiceConversationMode: boolean;
  toggleVoiceMode(): void;
  wakeWordEnabled: boolean;
  toggleWakeWord(): void;
};

export function ComposerToolbar({ listening, voicePhase, voiceNotice, voiceInputLevel, startMic, stopMic,
  monitoring, toggleMonitor, speechEnabled, setSpeechEnabled, providerDraft, setProviderDraft,
  setProviderModels, switchingProvider, lmStudioOnly, providerInfo, modelPickerValue,
  modelPickerOptions, showModelPicker, reasoningEffort, setReasoningEffort, thinkingEnabled,
  setThinkingEnabled, voiceReadAloud, toggleReadAloud, voiceConversationMode, toggleVoiceMode,
  wakeWordEnabled, toggleWakeWord }: ComposerToolbarProps) {
  const [toolbarSize, setToolbarSize] = useState<"full" | "icons" | "compact" | "mini">("full");
  const [toolbarMenuOpen, setToolbarMenuOpen] = useState(false);
  const toolbarObserverRef = useRef<ResizeObserver | null>(null);
  const toolbarRef = useCallback((el: HTMLDivElement | null) => {
    toolbarObserverRef.current?.disconnect();
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width;
      setToolbarSize(width >= 960 ? "full" : width >= 760 ? "icons" : width >= 600 ? "compact" : "mini");
    });
    observer.observe(el);
    toolbarObserverRef.current = observer;
  }, []);
  useEffect(() => { if (toolbarSize !== "mini") setToolbarMenuOpen(false); }, [toolbarSize]);
  useEffect(() => {
    if (!toolbarMenuOpen) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      const outside = event instanceof KeyboardEvent
        ? event.key === "Escape"
        : !(event.target as HTMLElement | null)?.closest?.(".toolbar-overflow");
      if (outside) setToolbarMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [toolbarMenuOpen]);

  return (
                    <div className="controls-row" ref={toolbarRef} data-size={toolbarSize}>
                      <div className="composer-primary-controls">
                        <div className="composer-tools-slot" role="group" aria-label="Input tools">
                        <button
                          className={`mic-button ${listening ? "active" : ""}`}
                          type="button"
                          title={listening ? "Stop microphone" : "Start microphone"}
                          aria-label={listening ? "Stop microphone" : "Start microphone"}
                          disabled={voicePhase === "transcribing" || voicePhase === "requesting_permission"}
                          onClick={() => listening ? stopMic() : startMic()}
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" />
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                        {(voicePhase !== "idle" || voiceNotice) ? (
                          <span
                            className="voice-transport-status"
                            data-state={voicePhase}
                            title={voiceNotice || voicePhase}
                            aria-live="polite"
                          >
                            <i style={{ transform: `scale(${1 + voiceInputLevel * 0.55})` }} />
                            {voicePhase === "requesting_permission"
                              ? "Mic access"
                              : voicePhase === "listening"
                              ? "Listening"
                              : voicePhase === "transcribing"
                              ? "Local transcript"
                              : voicePhase === "speaking"
                              ? "Speaking"
                              : voicePhase === "error"
                              ? "Voice setup"
                              : voiceNotice || "Voice ready"}
                          </span>
                        ) : null}
                        <button
                          className={`composer-square is-overflowable ${monitoring ? "active" : ""}`}
                          type="button"
                          title={monitoring ? "Stop screen monitor" : "Screen monitor"}
                          aria-label={monitoring ? "Stop screen monitor" : "Screen monitor"}
                          onClick={toggleMonitor}
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <rect x="2" y="4" width="20" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
                            <path d="M12 16v4M8 20h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                        <button
                          className={`composer-square is-overflowable ${!speechEnabled ? "active" : ""}`}
                          type="button"
                          title={speechEnabled ? "Sound on · click to mute" : "Sound off · click to unmute"}
                          aria-label={speechEnabled ? "Mute sound" : "Unmute sound"}
                          onClick={() => setSpeechEnabled(!speechEnabled)}
                        >
                          {speechEnabled ? (
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                              <path d="M4.5 10.5h3l4-3.5v10l-4-3.5h-3z" />
                              <path d="M15.5 9.5a4 4 0 0 1 0 5" />
                              <path d="M17.5 7.5a7 7 0 0 1 0 9" />
                            </svg>
                          ) : (
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                              <path d="M4.5 10.5h3l4-3.5v10l-4-3.5h-3z" />
                              <path d="M16 9.5 20 14.5M20 9.5 16 14.5" />
                            </svg>
                          )}
                        </button>
                        </div>
                        <div className="control-slot provider-slot" data-label="Provider">
                        <div className="inline-switcher">
                          <select
                            className="provider-picker"
                            value={providerDraft.provider}
                            onChange={(e) => {
                              const p = e.target.value;
                              setProviderModels([]);
                              setProviderDraft((d) => ({
                                ...d,
                                provider: p,
                                base_url: "",
                                model: p === "openai" ? openaiModelOptions[0] : p === "gemini" ? geminiModelOptions[0] : "",
                              }));
                            }}
                            disabled={switchingProvider || lmStudioOnly}
                            title="Model provider"
                            aria-label="Model provider"
                          >
                            {(providerInfo?.available_providers || fallbackProviders)
                              .filter((p) => !lmStudioOnly || p.id === "lmstudio")
                              .map((p) => (
                                <option key={p.id} value={p.id}>
                                  {p.name}
                                </option>
                              ))}
                          </select>
                        </div>
                        </div>
                        <div className="control-slot model-slot" data-label="Model">
                        <select
                          className="model-picker"
                          value={modelPickerValue}
                          onChange={(e) => {
                            if (!showModelPicker) return;
                            setProviderDraft((d) => ({ ...d, model: e.target.value }));
                          }}
                          disabled={switchingProvider || !showModelPicker}
                          title="Model"
                          aria-label="Model"
                        >
                          {modelPickerOptions.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))}
                        </select>
                        </div>
                        <div className="control-slot effort-slot is-overflowable" data-label="Effort">
                        <select
                          className="model-picker"
                          value={reasoningEffort}
                          onChange={(e: any) => setReasoningEffort(e.target.value as ReasoningEffort)}
                          title="Reasoning effort"
                          aria-label="Reasoning effort"
                        >
                          <option value="minimal">Minimal</option>
                          <option value="low">Low</option>
                          <option value="medium">Medium</option>
                          <option value="high">High</option>
                          <option value="extra_high">Extra High</option>
                          <option value="max">Max</option>
                          <option value="ultra">Ultra</option>
                        </select>
                      </div>
                      </div>
                      <div className="composer-mode-controls" role="group" aria-label="Thinking and voice controls">
                      <button
                        className={`composer-mode-button ${thinkingEnabled ? "active" : ""}`}
                        type="button"
                        title={thinkingEnabled ? "Thinking: on" : "Thinking: off"}
                        aria-label="Thinking"
                        aria-pressed={thinkingEnabled}
                        onClick={() => setThinkingEnabled(!thinkingEnabled)}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="3"/></svg>
                        <span className="composer-mode-label">Think</span>
                      </button>
                      <button
                        className={`composer-mode-button is-overflowable ${voiceReadAloud ? "active" : ""}`}
                        type="button"
                        title={voiceReadAloud ? "Read replies aloud: on" : "Read replies aloud: off"}
                        aria-label="Read replies aloud"
                        aria-pressed={voiceReadAloud}
                        onClick={toggleReadAloud}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M4 10h3l4-3v10l-4-3H4zM15 9a4 4 0 0 1 0 6M18 6a8 8 0 0 1 0 12"/></svg>
                        <span className="composer-mode-label">Read</span>
                      </button>
                      <button
                        className={`composer-mode-button is-overflowable ${voiceConversationMode ? "active" : ""}`}
                        type="button"
                        title={voiceConversationMode ? "Voice conversation: on" : "Voice conversation: off"}
                        aria-label="Voice conversation mode"
                        aria-pressed={voiceConversationMode}
                        onClick={toggleVoiceMode}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M5 10v4M9 7v10M13 4v16M17 7v10M21 10v4"/></svg>
                        <span className="composer-mode-label">Voice</span>
                      </button>
                      <button
                        className={`composer-mode-button is-overflowable ${wakeWordEnabled ? "active" : ""}`}
                        type="button"
                        title={wakeWordEnabled ? "Wake word: on (say “Hey Echo”)" : "Wake word: off"}
                        aria-label="Wake word"
                        aria-pressed={wakeWordEnabled}
                        onClick={toggleWakeWord}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="12" cy="12" r="3"/><path d="M12 2a10 10 0 0 1 10 10M12 22A10 10 0 0 1 2 12M5 5a10 10 0 0 1 14 14"/></svg>
                        <span className="composer-mode-label">Wake</span>
                      </button>
                      {toolbarSize === "mini" ? (
                        <div className="toolbar-overflow">
                          <button
                            type="button"
                            className={`composer-mode-button${toolbarMenuOpen ? " active" : ""}`}
                            title="More controls"
                            aria-label="More controls"
                            aria-haspopup="menu"
                            aria-expanded={toolbarMenuOpen}
                            onClick={() => setToolbarMenuOpen((v) => !v)}
                          >
                            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></svg>
                          </button>
                          {toolbarMenuOpen ? (
                            <div className="toolbar-menu" role="menu" aria-label="More controls">
                              <button type="button" role="menuitemcheckbox" aria-checked={voiceReadAloud} onClick={toggleReadAloud}>
                                <span>Read replies aloud</span><i data-on={voiceReadAloud ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={voiceConversationMode} onClick={toggleVoiceMode}>
                                <span>Voice conversation</span><i data-on={voiceConversationMode ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={monitoring} onClick={toggleMonitor}>
                                <span>Screen monitor</span><i data-on={monitoring ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={speechEnabled} onClick={() => setSpeechEnabled(!speechEnabled)}>
                                <span>Sound</span><i data-on={speechEnabled ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={wakeWordEnabled} onClick={toggleWakeWord}>
                                <span>Wake word</span><i data-on={wakeWordEnabled ? "true" : "false"} />
                              </button>
                              <label className="toolbar-menu-select">
                                <span>Effort</span>
                                <select value={reasoningEffort} onChange={(e: any) => setReasoningEffort(e.target.value as ReasoningEffort)} aria-label="Reasoning effort">
                                  <option value="minimal">Minimal</option>
                                  <option value="low">Low</option>
                                  <option value="medium">Medium</option>
                                  <option value="high">High</option>
                                  <option value="extra_high">Extra High</option>
                                  <option value="max">Max</option>
                                  <option value="ultra">Ultra</option>
                                </select>
                              </label>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      </div>
                    </div>
  );
}