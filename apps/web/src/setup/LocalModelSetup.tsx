import React, { useEffect, useState } from "react";
import { creationRequest } from "../creations/api";
import type { SettingsMap } from "../settings/useSettings";

type Runtime = { provider: string; ready: boolean; detail: string; presets: { id: string; label: string; detail: string }[] };
type Job = { id: string; provider: string; model: string; base_url: string; status: string; progress: number; detail: string };
export function LocalModelSetup({ apiBase, settings, save }: { apiBase: string; settings: SettingsMap; save(patch: SettingsMap): Promise<void> }) {
  const [runtimes, setRuntimes] = useState<Runtime[]>([]);
  const [choice, setChoice] = useState("ollama|qwen2.5:3b");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const detect = () => creationRequest(apiBase, "/onboarding/local-models").then(data => setRuntimes(data.items)).catch(e => setError(e.message));
  useEffect(() => { void detect(); }, [apiBase]);
  useEffect(() => {
    if (!job || !["downloading", "loading"].includes(job.status)) return;
    let disposed = false;
    const timer = setInterval(() => { void creationRequest(apiBase, `/onboarding/local-models/jobs/${job.id}`).then(data => { if (!disposed) setJob(data); }).catch(e => { if (!disposed) setError(e.message); }); }, 1500);
    return () => { disposed = true; clearInterval(timer); };
  }, [apiBase, job?.id, job?.status]);
  if (settings.use_local_models === false) return null;
  const [provider, model] = choice.split("|");
  const runtime = runtimes.find(item => item.provider === provider);
  const pending = busy || !!job && ["downloading", "loading"].includes(job.status);
  return <section className="local-model-setup"><h3>Need a local model?</h3><p>Download and load through your installed runtime. Start its server first. Nothing downloads automatically.</p>
    <select aria-label="Setup model" value={choice} disabled={pending} onChange={e => setChoice(e.target.value)}>{runtimes.flatMap(r => r.presets.map(p => <option key={`${r.provider}|${p.id}`} value={`${r.provider}|${p.id}`}>{r.provider === "ollama" ? "Ollama" : "LM Studio"} · {p.label}</option>))}</select>
    <p>{runtime?.presets.find(p => p.id === model)?.detail} {runtime?.detail}</p>
    <div className="creation-actions"><button className="es-btn" disabled={pending || !runtime?.ready} onClick={async () => { setBusy(true); setError(""); try { setJob(await creationRequest(apiBase, "/onboarding/local-models/jobs", "POST", { provider, model })); } catch (e) { setError(String(e)); } finally { setBusy(false); } }}>Download and load</button><button className="es-btn" disabled={pending} onClick={() => void detect()}>Check runtimes again</button></div>
    {job && <p role="status">{job.detail}</p>}{job && job.progress > 0 && job.status === "downloading" && <progress max={1} value={job.progress} aria-label="Model download progress" />}
    {job?.status === "completed" && <button className="es-btn es-btn-primary" onClick={async () => { try { await save({ use_local_models: true, local: { provider: job.provider, model_name: job.model, base_url: job.base_url + (job.provider === "lmstudio" ? "/v1" : "") } }); } catch (e) { setError(String(e)); } }}>Use this model</button>}
    {error && <p role="alert">{error}</p>}<small>Speed and memory use depend on your hardware. You’ll verify a response before starting your first chat.</small>
  </section>;
}
