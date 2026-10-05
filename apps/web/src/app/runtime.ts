// Moved out of index.tsx (10.0 split). Kept verbatim.
import { create } from "zustand";
import { localVoicePlayback } from "../voiceTransport";
import { liveAudioPlayback } from "../liveVoiceTransport";
import { type AppState, type AvatarConfig, type ProviderInfo, type ProviderListItem } from "./types";

export const cloudProviders = ["openai", "gemini", "anthropic", "xai"];
export const listableProviders = ["ollama", "lmstudio", "localai", "vllm"];
export const isLmStudioOnlyLocked = (info: ProviderInfo | null): boolean => {
  const providers = info?.available_providers || [];
  if (!providers.length) return false;
  return providers.length === 1 && providers[0].id === "lmstudio";
};

export const sleepMs = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Fetch with timeout. Safe GETs may honor Retry-After once on 429; mutations never auto-replay. */
export const fetchWithTimeout = async (
  url: string,
  init?: RequestInit,
  timeoutMs: number = 4500,
  options?: { retrySafeGetOn429?: boolean },
) => {
  const method = String(init?.method || "GET").toUpperCase();
  const allowRetry = options?.retrySafeGetOn429 !== false && method === "GET";
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response = await fetch(url, { ...init, signal: controller.signal });
    if (allowRetry && response.status === 429) {
      const retryAfterRaw = response.headers.get("Retry-After");
      const retryAfterSec = Math.min(15, Math.max(1, Number(retryAfterRaw || 2) || 2));
      await sleepMs(retryAfterSec * 1000);
      response = await fetch(url, { ...init, signal: controller.signal });
    }
    return response;
  } finally {
    clearTimeout(id);
  }
};

export const normalizeTimestampMs = (value: unknown): number => {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return Date.now();
  return num < 1_000_000_000_000 ? num * 1000 : num;
};

export const isEmptySessionDraft = (session: { name?: string; messageCount?: number }): boolean =>
  Number(session.messageCount || 0) === 0 &&
  /^(?:new|default)?\s*(?:session|thread)(?:\s+\d+)?$/i.test(String(session.name || "").trim());

export const fallbackProviders: ProviderListItem[] = [
  { id: "openai", name: "OpenAI", local: false, description: "OpenAI GPT models" },
  { id: "gemini", name: "Google Gemini", local: false, description: "Google Gemini models" },
  { id: "anthropic", name: "Claude", local: false, description: "Anthropic Claude models" },
  { id: "xai", name: "Grok", local: false, description: "xAI Grok models" },
  { id: "ollama", name: "Ollama", local: true, description: "Local Ollama models" },
  { id: "lmstudio", name: "LM Studio (GGUF direct)", local: true, description: "LM Studio (GGUF direct via OpenAI-compatible API)" },
  { id: "localai", name: "LocalAI", local: true, description: "LocalAI (OpenAI compatible)" },
  { id: "vllm", name: "vLLM", local: true, description: "vLLM (OpenAI compatible)" },
  { id: "llama_cpp", name: "llama.cpp", local: true, description: "llama.cpp (local + OpenAI compatible)" },
];

export const useAppStore = create<AppState>((set) => ({
  messages: [],
  streaming: false,
  listening: false,
  speaking: false,
  speechEnabled: true,
  setSpeechEnabled: (v) => { if (!v) stopTts(); set({ speechEnabled: v }); },
  speechBeat: 0,
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setStreaming: (v) => set({ streaming: v }),
  setListening: (v) => set({ listening: v }),
  setSpeaking: (v) => set({ speaking: v }),
  bumpSpeechBeat: () => set((s) => ({ speechBeat: s.speechBeat + 1 })),
}));

export const colors = {
  bg: "#000000",
  panel: "#0a0a0a",
  panel2: "#111111",
  accent: "#ffffff",
  accentSoft: "#222222",
  text: "#ffffff",
  textDim: "#888888",
  line: "#333333",
  danger: "#ff4444",
  glow: "#ffffff",
};

export const defaultAvatarConfig: AvatarConfig = {
  body_color: "#ffffff",
  eye_color: "#000000",
  bg_color: "#0a0a0a",
  glow_color: "#4f8eff",
  idle_activity: "auto",
  breathing_speed: 1,
  eye_size: 1,
  body_roundness: 14,
  enable_glow: true,
  enable_idle_activities: true,
  custom_status_text: "",
};

export const sanitizeForTTS = (input: string) => {
  let text = input || "";
  text = text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, "");
  text = text.replace(/[\u2300-\u23FF\u2600-\u27BF]/g, "");
  text = text.replace(/[\u200D\uFE0E\uFE0F]/g, "");
  return text.replace(/\s+/g, " ").trim();
};

export const stopTts = () => {
  localVoicePlayback.stop();
  liveAudioPlayback.stop();
  useAppStore.getState().setSpeaking(false);
};

