import React, { useEffect, useRef, useState } from "react";
import { Group, Row, SecretField, Select } from "./controls";
import type { SettingsMap } from "./useSettings";

type Save = (patch: Record<string, unknown>) => void | Promise<void>;
type Voice = { voice_id: string; name: string; category: string; description: string };

/** Models offered for ElevenLabs read-aloud (backend: agent/elevenlabs.py). */
export const ELEVENLABS_MODELS = [
  { value: "eleven_flash_v2_5", label: "Flash v2.5 · fastest, lowest cost" },
  { value: "eleven_turbo_v2_5", label: "Turbo v2.5 · fast, higher quality" },
  { value: "eleven_multilingual_v2", label: "Multilingual v2 · most natural" },
  { value: "eleven_v3", label: "v3 · most expressive" },
];

/** Plays a short sample from the chosen read-aloud voice (POST /media-runtime/voice/preview). */
export function TestVoice({ apiBase, provider, voiceId = "", disabled = false }: { apiBase: string; provider: string; voiceId?: string; disabled?: boolean }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [error, setError] = useState("");
  const audio = useRef<HTMLAudioElement | null>(null);
  const request = useRef<{ id: string; controller: AbortController } | null>(null);
  const objectUrl = useRef("");
  const release = () => {
    audio.current?.pause();
    audio.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = "";
    const current = request.current;
    request.current = null;
    current?.controller.abort();
    if (current) void fetch(`${apiBase}/media-runtime/voice/preview/${current.id}/cancel`, { method: "POST" }).catch(() => undefined);
  };
  useEffect(() => { setState("idle"); setError(""); return () => { release(); }; }, [apiBase, provider, voiceId]);
  const play = async () => {
    if (state !== "idle") { release(); setState("idle"); return; }
    setState("loading");
    setError("");
    const current = { id: crypto.randomUUID(), controller: new AbortController() };
    request.current = current;
    try {
      const res = await fetch(`${apiBase}/media-runtime/voice/preview`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        signal: current.controller.signal,
        body: JSON.stringify({ provider_id: provider, voice_id: voiceId, request_id: current.id }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(String(data?.detail || `The voice couldn't speak (${res.status}).`));
      }
      const url = URL.createObjectURL(await res.blob());
      if (request.current !== current) { URL.revokeObjectURL(url); return; }
      objectUrl.current = url;
      const clip = new Audio(url);
      audio.current = clip;
      clip.onended = () => { if (request.current === current) { request.current = null; release(); setState("idle"); } };
      await clip.play();
      if (request.current === current) setState("playing");
    } catch (e) {
      if (request.current !== current) return;
      release();
      setState("idle");
      setError(e instanceof Error ? e.message : "The voice couldn't speak.");
    }
  };
  return (
    <Row label="Test the voice" help={error || "Plays one sentence with the voice above. It's what the speaker button under each reply uses."}>
      <button type="button" className="es-btn es-btn-sm" disabled={disabled && state === "idle"} onClick={() => void play()}>
        {state === "loading" ? "Cancel sample" : state === "playing" ? "Stop" : "Play sample"}
      </button>
    </Row>
  );
}

/** ElevenLabs account, voice, model and speed for reading replies aloud. */
export function ElevenLabsSettings({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const hasKey = Boolean(s.elevenlabs_api_key);
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!hasKey) { setVoices(null); return; }
    let live = true;
    setError("");
    fetch(`${apiBase}/media-runtime/voice/elevenlabs/voices`, { cache: "no-store" })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(String(data?.detail || `HTTP ${res.status}`));
        if (live) setVoices(Array.isArray(data?.items) ? data.items : []);
      })
      .catch((e) => { if (live) { setVoices([]); setError(e instanceof Error ? e.message : String(e)); } });
    return () => { live = false; };
  }, [apiBase, hasKey, s.elevenlabs_api_key]);

  const selected = String(s.elevenlabs_voice_id || "");
  const voiceOptions = [
    { value: "", label: "Default · George" },
    ...(voices || []).map((v) => ({ value: v.voice_id, label: v.description ? `${v.name} · ${v.description}` : v.name })),
    ...(selected && voices && !voices.some((v) => v.voice_id === selected) ? [{ value: selected, label: `Voice ${selected}` }] : []),
  ];
  return (
    <Group title="ElevenLabs" description="A cloud voice for reading replies aloud. Choose ElevenLabs under Read aloud above. Each reply read aloud sends its text to ElevenLabs and uses characters on your plan; Private and Offline modes turn it off.">
      <Row label="API key" help="From elevenlabs.io › Developers › API keys. Stored encrypted on this PC." stack>
        <SecretField isSet={hasKey} onCommit={(v) => save({ elevenlabs_api_key: v.trim() })} />
      </Row>
      <Row label="Voice" help={error || (hasKey ? (voices ? "Voices on your ElevenLabs account." : "Loading your voices…") : "Add your API key to see your voices.")}>
        {hasKey ? (
          <Select value={selected} onChange={(v) => save({ elevenlabs_voice_id: v })} options={voiceOptions} />
        ) : <span className="st-muted">{hasKey ? "—" : "No key yet"}</span>}
      </Row>
      <Row label="Model">
        <Select value={String(s.elevenlabs_model || "eleven_flash_v2_5")} onChange={(v) => save({ elevenlabs_model: v })} options={ELEVENLABS_MODELS} />
      </Row>
      <Row label="Speed">
        <Select value={String(s.elevenlabs_speed ?? 1)} onChange={(v) => save({ elevenlabs_speed: Number(v) })}
          options={[{ value: "0.85", label: "Slower" }, { value: "1", label: "Normal" }, { value: "1.1", label: "A bit faster" }, { value: "1.2", label: "Faster" }]} />
      </Row>
      {hasKey ? <TestVoice apiBase={apiBase} provider="elevenlabs" voiceId={selected} /> : null}
    </Group>
  );
}
