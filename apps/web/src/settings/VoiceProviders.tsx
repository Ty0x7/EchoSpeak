import React, { useEffect, useState } from "react";
import { Group, Row, Select, Status, TextField } from "./controls";
import { ElevenLabsSettings, TestVoice } from "./VoiceOutput";
import type { SettingsMap } from "./useSettings";
import { DramaSettings } from "./DramaSettings";

export const SPEECH_PROVIDERS = [
  { value: "windows-sapi", label: "Windows voices · local, lightweight" },
  { value: "piper-local", label: "Piper · local" },
  { value: "drama3-local", label: "Drama-3 · local, large download" },
  { value: "elevenlabs", label: "ElevenLabs · cloud" },
];
type Provider = { id: string; detail: string; operation_readiness: Record<string, boolean> };
type Save = (patch: Record<string, unknown>) => void | Promise<void>;

/** One provider selection, shared by Settings and optional first-run setup. */
export function VoiceProviders({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const selected = String(s.voice_local_tts_provider || "windows-sapi");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch(`${apiBase}/media-runtime/voice/providers`);
        if (!response.ok) throw new Error("Voice status is unavailable. Try checking again.");
        const data = await response.json();
        if (active) { setProviders(data.items || []); setError(""); }
      } catch (e) { if (active) setError(String(e instanceof Error ? e.message : e)); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 6000);
    return () => { active = false; window.clearInterval(timer); };
  }, [apiBase, selected, s.voice_piper_model_path, s.elevenlabs_api_key, revision]);
  const provider = providers.find(p => p.id === selected);
  const ready = Boolean(provider?.operation_readiness.text_to_speech);
  return <>
    <Group title="Read aloud" description="The voice reads Echo's replies. Your AI model still handles the conversation, whether it runs locally or in the cloud.">
      <Row label="Speech provider" help="Used by the speaker button, automatic read-aloud and standard voice conversations. Native Gemini Live uses its own audio.">
        <Select value={selected} onChange={v => save({ voice_local_tts_provider: v })} options={SPEECH_PROVIDERS} />
      </Row>
      <Row label={<Status tone={error ? "err" : ready ? "ok" : "warn"}>{error ? "Status unavailable" : ready ? "Configured" : provider ? "Setup needed" : "Checking voice…"}</Status>}
        help={error || provider?.detail || "Checking does not load or download a speech model."}>
        <button className="es-btn es-btn-sm" onClick={() => setRevision(v => v + 1)}>Check again</button>
      </Row>
      {selected !== "drama3-local" && selected !== "elevenlabs" ? <TestVoice apiBase={apiBase} provider={selected} disabled={!ready} /> : null}
    </Group>
    {selected === "windows-sapi" ? <Group title="Windows voice" description="Uses speech already installed on this PC. No model download or cloud account is needed.">
      <Row label="Voice name" help="Leave blank to use your Windows default. An exact installed System.Speech voice name can be entered here.">
        <TextField value={String(s.voice_windows_voice || "")} onCommit={v => save({ voice_windows_voice: v.trim() })} placeholder="Windows default" />
      </Row>
    </Group> : null}
    {selected === "piper-local" ? <Group title="Piper voice" description="Use your existing Piper executable on PATH and an installed ONNX voice with its matching .onnx.json file.">
      <Row label="Voice model" help="Full path to the .onnx voice on this PC." stack>
        <TextField wide mono value={String(s.voice_piper_model_path || "")} onCommit={v => save({ voice_piper_model_path: v.trim() })} placeholder="C:\Voices\voice.onnx" />
      </Row>
    </Group> : null}
    {selected === "elevenlabs" ? <ElevenLabsSettings s={s} save={save} apiBase={apiBase} /> : null}
    {selected === "drama3-local" ? <DramaSettings s={s} save={save} apiBase={apiBase} /> : null}
  </>;
}
