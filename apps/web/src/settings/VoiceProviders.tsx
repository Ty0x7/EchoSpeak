import React, { useEffect, useState } from "react";
import { Group, Row, Select, Status, TextField } from "./controls";
import { ElevenLabsSettings, TestVoice } from "./VoiceOutput";
import type { SettingsMap } from "./useSettings";
import { DramaSettings } from "./DramaSettings";

export const SPEECH_PROVIDERS = [
  { value: "kokoro-local", label: "Kokoro · local, CPU, natural" },
  { value: "windows-sapi", label: "Windows voices · local, lightweight" },
  { value: "piper-local", label: "Piper · local" },
  { value: "drama3-local", label: "Drama-3 · local, large download" },
  { value: "elevenlabs", label: "ElevenLabs · cloud" },
];
type Provider = { id: string; detail: string; operation_readiness: Record<string, boolean> };
type Save = (patch: Record<string, unknown>) => void | Promise<void>;

/** One provider selection, shared by Settings and optional first-run setup. */
export function VoiceProviders({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const isWindows = typeof navigator !== "undefined" && /win/i.test(navigator.platform || "");
  const selected = String(s.voice_local_tts_provider || (isWindows ? "windows-sapi" : "kokoro-local"));
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
        <TextField wide mono value={String(s.voice_piper_model_path || "")} onCommit={v => save({ voice_piper_model_path: v.trim() })} placeholder={isWindows ? "C:\\Voices\\voice.onnx" : "~/.local/share/echospeak/models/tts/piper/en_US-lessac-medium.onnx"} />
      </Row>
    </Group> : null}
    {selected === "kokoro-local" ? <KokoroSettings s={s} save={save} apiBase={apiBase} ready={ready} /> : null}
    <SpeakerSettings s={s} save={save} apiBase={apiBase} ready={ready} />
    {selected === "elevenlabs" ? <ElevenLabsSettings s={s} save={save} apiBase={apiBase} /> : null}
    {selected === "drama3-local" ? <DramaSettings s={s} save={save} apiBase={apiBase} /> : null}
  </>;
}

function KokoroSettings({ s, save, apiBase, ready }: { s: SettingsMap; save: Save; apiBase: string; ready: boolean }) {
  const [voices, setVoices] = useState<string[]>([]);
  useEffect(() => {
    if (!ready) return;
    let active = true;
    fetch(`${apiBase}/media-runtime/voice/kokoro/voices`).then(r => r.ok ? r.json() : { items: [] })
      .then(d => { if (active) setVoices(d.items || []); }).catch(() => undefined);
    return () => { active = false; };
  }, [apiBase, ready]);
  const current = String(s.voice_kokoro_voice || "af_heart");
  const options = (voices.length ? voices : [current]).map(v => ({ value: v, label: kokoroLabel(v) }));
  return <Group title="Kokoro voice" description="Runs on the CPU, so speech never takes GPU memory from your chat model. Works offline once the model is on disk.">
    <Row label="Voice" help="American (a…) and British (b…) English voices; f is female, m is male.">
      <Select value={current} onChange={v => save({ voice_kokoro_voice: v })} options={options} disabled={!ready} />
    </Row>
    {!ready ? <KokoroDownload apiBase={apiBase} /> : null}
  </Group>;
}

function kokoroLabel(id: string) {
  const accent = id.startsWith("b") ? "British" : id.startsWith("a") ? "American" : "Other";
  const kind = id[1] === "f" ? "female" : id[1] === "m" ? "male" : "";
  const name = id.split("_").slice(1).join(" ");
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} · ${accent}${kind ? ` ${kind}` : ""}`;
}

type Device = { name: string; description: string; default: boolean; muted: boolean };

/** Shared by Echo and Echo Companion: speed, volume, output device and a real speaker test. */
function SpeakerSettings({ s, save, apiBase, ready }: { s: SettingsMap; save: Save; apiBase: string; ready: boolean }) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [test, setTest] = useState("");
  useEffect(() => {
    let active = true;
    fetch(`${apiBase}/media-runtime/voice/output-devices`).then(r => r.ok ? r.json() : { items: [] })
      .then(d => { if (active) setDevices(d.items || []); }).catch(() => undefined);
    return () => { active = false; };
  }, [apiBase]);
  const speed = Number(s.voice_speed || 1);
  const volume = Number(s.voice_volume ?? 1);
  const speakTest = async () => {
    setTest("Speaking…");
    try {
      const res = await fetch(`${apiBase}/media-runtime/voice/speak`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "This is Echo, speaking through your speakers.", channel: "preview", interrupt: true }),
      });
      setTest(res.ok ? "Sent to your speakers. If you heard nothing, check the output and volume." : "The voice isn't ready.");
    } catch { setTest("Couldn't reach EchoSpeak."); }
  };
  const stop = () => void fetch(`${apiBase}/media-runtime/voice/speak/cancel`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(() => setTest(""));
  const deviceOptions = [{ value: "", label: "System default" }, ...devices.map(d => ({ value: d.name, label: `${d.description}${d.muted ? " (muted)" : ""}` }))];
  return <Group title="Speakers" description="Echo and Echo Companion share one voice queue, so they never talk over each other.">
    <Row label="Speaking speed" help={`${speed.toFixed(2)}×`}>
      <input type="range" min={0.7} max={1.4} step={0.05} value={speed} aria-label="Speaking speed"
        onChange={e => save({ voice_speed: Number(e.target.value) })} />
    </Row>
    <Row label="Volume" help={`${Math.round(volume * 100)}%`}>
      <input type="range" min={0} max={1.5} step={0.05} value={volume} aria-label="Volume"
        onChange={e => save({ voice_volume: Number(e.target.value) })} />
    </Row>
    <Row label="Output" help="Where speech plays on this computer.">
      <Select value={String(s.voice_output_device || "")} onChange={v => save({ voice_output_device: v })} options={deviceOptions} />
    </Row>
    <Row label="Test speakers" help={test || "Plays a short sentence on this computer through the shared queue."}>
      <span style={{ display: "flex", gap: 6 }}>
        <button className="es-btn es-btn-sm" disabled={!ready} onClick={() => void speakTest()}>Speak</button>
        <button className="es-btn es-btn-sm" onClick={stop}>Stop</button>
      </span>
    </Row>
  </Group>;
}

function KokoroDownload({ apiBase }: { apiBase: string }) {
  const [status, setStatus] = useState<{ state: string; done: number; total: number; error: string; detail?: string }>({ state: "idle", done: 0, total: 0, error: "" });
  useEffect(() => {
    if (status.state !== "running") return;
    const timer = window.setInterval(() => {
      void fetch(`${apiBase}/media-runtime/voice/kokoro/setup`).then(r => r.ok ? r.json() : null).then(d => d && setStatus(d)).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [apiBase, status.state]);
  const start = async () => {
    const res = await fetch(`${apiBase}/media-runtime/voice/kokoro/setup`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    setStatus(res.ok ? data : { state: "failed", done: 0, total: 0, error: String(data.detail || "Download refused.") });
  };
  const mb = (n: number) => `${Math.round(n / 1048576)} MB`;
  const help = status.state === "running" ? `Downloading… ${mb(status.done)}${status.total ? ` of ${mb(status.total)}` : ""}`
    : status.state === "done" ? "Downloaded. Kokoro is ready."
    : status.state === "failed" ? status.error
    : "One-time download of 192 MB from the kokoro-onnx release, checked by SHA-256. Offline and Private mode block it.";
  return <Row label="Kokoro voice files" help={help}>
    <button className="es-btn es-btn-sm" disabled={status.state === "running"} onClick={() => void start()}>
      {status.state === "running" ? "Downloading…" : "Download"}
    </button>
  </Row>;
}
