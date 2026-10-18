import React, { useEffect, useState } from "react";
import { Group, Row, Select, Status, TextField } from "./controls";
import { TestVoice } from "./VoiceOutput";
import type { SettingsMap } from "./useSettings";

type Save = (patch: Record<string, unknown>) => void | Promise<void>;
type Setup = {
  ready: boolean; active: boolean; runtime_verified: boolean; detail: string; models_dir: string; build_supported: boolean;
  job: { running: boolean; kind: string; message: string; progress: number; downloaded: number; total: number; error: string };
  hardware: { ram_bytes: number; disk_free_bytes: number; gpus: { index: number; name: string; vram_mb: number; free_vram_mb: number }[] };
  models: { id: string; label: string; size: number; installed: boolean; cpu_layers: number }[];
};
const gib = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GiB`;

export function DramaSettings({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const [info, setInfo] = useState<Setup | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState("q2");
  const [importPath, setImportPath] = useState("");
  const [runtimePath, setRuntimePath] = useState(String(s.voice_drama_runtime_path || ""));
  useEffect(() => setRuntimePath(String(s.voice_drama_runtime_path || "")), [s.voice_drama_runtime_path]);
  const [accepted, setAccepted] = useState(false);
  const [revision, setRevision] = useState(0);
  const lastRunning = React.useRef(false);
  const refresh = async () => {
    const response = await fetch(`${apiBase}/media-runtime/voice/drama3`);
    const data = await response.json();
    if (!response.ok) throw new Error(String(data.detail || "Could not check Drama-3 setup."));
    setInfo(data);
    if (lastRunning.current && !data.job.running) window.dispatchEvent(new Event("echospeak.voice-settings-changed"));
    lastRunning.current = Boolean(data.job.running);
  };
  useEffect(() => {
    let active = true;
    const poll = () => { if (active) void refresh().catch(e => { if (active) setError(String(e.message || e)); }); };
    poll();
    const timer = window.setInterval(poll, info?.job.running ? 1200 : 6000);
    return () => { active = false; window.clearInterval(timer); };
  }, [apiBase, revision, info?.job.running]);
  const action = async (payload: Record<string, unknown>) => {
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${apiBase}/media-runtime/voice/drama3`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(String(data.detail || "Setup could not complete."));
      setMessage(data.message || "Setup started.");
      await refresh();
      setRevision(v => v + 1);
      // Managed jobs set paths themselves; ask the shared settings owner to reload.
      window.dispatchEvent(new Event("echospeak.voice-settings-changed"));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const running = Boolean(info?.job.running);
  const backend = String(s.voice_drama_backend || "cpu");
  const selected = info?.models.find(m => m.id === model);
  return <>
    <Group title="Drama-3" description="Optional local speech. Built with Fish Audio. These experimental models need substantial memory; Windows voices and Piper are lighter options.">
      <Row label={<Status tone={info?.ready ? "ok" : "warn"}>{info?.active ? "Speaking locally" : info?.ready ? "Configured · sample still matters" : "Setup needed"}</Status>} help={info?.detail || "Checking files without loading the model…"} />
      <Row label="Your PC" help="Download size is not RAM or VRAM use. Offloaded weights need host RAM, and a local chat model competes for the same memory.">
        <span className="st-muted">{info?.hardware.ram_bytes ? `${gib(info.hardware.ram_bytes)} RAM · ` : ""}{info ? `${gib(info.hardware.disk_free_bytes)} disk free` : "Checking…"}</span>
      </Row>
      {info?.hardware.gpus.map(gpu => <Row key={gpu.index} label={gpu.name} help={`GPU ${gpu.index} · ${(gpu.vram_mb / 1024).toFixed(1)} GiB VRAM; ${(gpu.free_vram_mb / 1024).toFixed(1)} GiB currently free. Compatibility is confirmed by a sample, not this number.`} />)}
      {(error || info?.job.error) ? <Row label={<span role="alert">{error || info?.job.error}</span>} /> : null}
      {message ? <Row label={<span role="status">{message}</span>} /> : null}
      {running ? <Row label={info?.job.message || "Working…"} help={info?.job.total ? `${gib(info.job.downloaded)} of ${gib(info.job.total)} · ${Math.round(info.job.progress * 100)}%` : "Build progress depends on your installed compiler and runtime dependencies."}>
        <button className="es-btn es-btn-sm" disabled={busy} onClick={() => void action({ action: "cancel" })}>Cancel setup</button>
      </Row> : null}
    </Group>
    <Group title="1. Speech runtime" description="Drama-3 needs an audio.cpp build with fish_drama3 enabled. A regular GGUF chat runtime cannot run it.">
      <Row label="audio.cpp executable" help="Choose audiocpp_cli.exe from a build you trust. Keep its DLLs in the same folder. Verification runs a small loader check, without loading a model." stack>
        <TextField wide mono value={runtimePath} onCommit={v => { setRuntimePath(v.trim()); void save({ voice_drama_runtime_path: v.trim() }); }} placeholder="C:\audio.cpp\bin\audiocpp_cli.exe" />
      </Row>
      <Row label="Check runtime" help={info?.runtime_verified ? "Drama-3 speech support was verified for this executable." : "Older builds may start successfully while lacking Drama-3 support."}>
        <button className="es-btn es-btn-sm" disabled={busy || running} onClick={() => void action({ action: "verify", path: runtimePath })}>Verify runtime</button>
      </Row>
      <details className="st-voice-details"><summary>Install a compatible runtime</summary>
        <Row label="Build on this PC" help="Uses a pinned upstream source revision. Requires Git, Visual Studio 2022 C++ tools, CMake and Ninja. CUDA also needs its toolkit. No system tools are installed automatically.">
          <button className="es-btn es-btn-sm" disabled={busy || running || !info?.build_supported} onClick={() => void action({ action: "build", backend })}>Build {backend === "cuda" ? "CUDA" : "CPU"} runtime</button>
        </Row>
        <Row label="Build requirements"><a href="https://github.com/0xShug0/audio.cpp/blob/e065d66b7aa70bb697e39b1e7a952197f017baef/docs/models/fish_drama3.md" target="_blank" rel="noreferrer">audio.cpp Windows instructions</a></Row>
      </details>
    </Group>
    <Group title="2. Speech model" description="Choose a quantization to download, or link an existing compatible GGUF without copying it. No weights are included in EchoSpeak's installer.">
      <Row label="Model license" help={<><a href="https://huggingface.co/Jmica/Drama-3-GGUF/blob/17a5b40c5dc2c2be986f5371a7e650108b828e29/LICENSE" target="_blank" rel="noreferrer">Fish Audio Community License</a> has use and commercial restrictions. Use voice references only when you have the right to use that voice.</>}>
        <label className="st-voice-license"><input type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)} />I have read and accept the model license</label>
      </Row>
      <Row label="Download model" help="Q2 is the smallest download, not a guarantee it fits your PC. Resume retains verified partial downloads.">
        <Select value={model} onChange={setModel} options={(info?.models || []).map(m => ({ value: m.id, label: `${m.label} · ${gib(m.size)}` }))} />
      </Row>
      <Row label={selected ? `${gib(selected.size)} download` : "Choose a model"} help={info?.models_dir ? `Stored in ${info.models_dir}` : undefined}>
        <button className="es-btn es-btn-sm" disabled={busy || running || !accepted || !selected} onClick={() => void action({ action: "download", model, accept_license: accepted })}>{selected?.installed ? "Verify & use file" : "Download / Resume"}</button>
      </Row>
      <Row label="Use an existing GGUF" help="Existing files stay where they are. Import checks the Drama-3 family; it cannot certify the source of a file." stack>
        <TextField wide mono value={importPath} onCommit={setImportPath} placeholder="C:\Models\drama-3.gguf" />
        <button className="es-btn es-btn-sm" disabled={busy || running || !accepted} onClick={() => void action({ action: "import", path: importPath, accept_license: accepted })}>Link model</button>
      </Row>
      <Row label="Current model" help={String(s.voice_drama_model_path || "No model selected yet")} />
    </Group>
    <Group title="3. Voice and playback" description="The native worker runs only for a speech request and unloads afterward. The first sample may take time; cancelling stops its worker. Long replies use bounded clips, rather than live PCM streaming.">
      <Row label="Run speech on" help="CPU is more widely compatible but can be slow. CUDA requires a compatible build and NVIDIA GPU.">
        <Select value={backend} onChange={v => save({ voice_drama_backend: v })} options={[{ value: "cpu", label: "CPU" }, { value: "cuda", label: "NVIDIA CUDA" }]} />
      </Row>
      <details className="st-voice-details"><summary>Memory and voice reference</summary>
        <Row label="GPU" help="Used for CUDA speech. The model still needs host memory.">
          <Select value={String(s.voice_drama_device || 0)} onChange={v => save({ voice_drama_device: Number(v) })} options={(info?.hardware.gpus.length ? info.hardware.gpus : [{ index: 0, name: "GPU 0" }]).map(gpu => ({ value: String(gpu.index), label: gpu.name }))} />
        </Row>
        <Row label="CPU expert layers" help="0–40. More layers reduce GPU demand but can be much slower. Download presets apply upstream starting values; physical 16/24 GiB GPU compatibility is not established.">
          <TextField type="number" value={Number(s.voice_drama_cpu_layers ?? 8)} onCommit={v => { const n = Number(v); if (Number.isInteger(n) && n >= 0 && n <= 40) void save({ voice_drama_cpu_layers: n }); }} />
        </Row>
        <Row label="Reference WAV (optional)" help="Local PCM WAV, at most 30 seconds and 5 MiB. Leave blank for the model's default voice. The reference stays on this PC." stack>
          <TextField wide mono value={String(s.voice_drama_reference_path || "")} onCommit={v => save({ voice_drama_reference_path: v.trim() })} />
        </Row>
        <Row label="Reference transcript" help="Exact words spoken in your reference. No automatic emotional tags are added." stack>
          <TextField wide value={String(s.voice_drama_reference_text || "")} onCommit={v => save({ voice_drama_reference_text: v })} />
        </Row>
      </details>
      <TestVoice apiBase={apiBase} provider="drama3-local" disabled={!info?.ready || running} />
    </Group>
  </>;
}
