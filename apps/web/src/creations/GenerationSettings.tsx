import React, { useEffect, useState } from "react";
import { Group, Row, SecretField, Select, TextField, Toggle } from "../settings/controls";
import type { SettingsMap } from "../settings/useSettings";
import { creationRequest } from "./api";

export function GenerationSettings({ s, save, apiBase }: { s: SettingsMap; save(patch: SettingsMap): Promise<void>; apiBase: string }) {
  const [providers, setProviders] = useState<any[]>([]);
  const [local, setLocal] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    try { const [p, l] = await Promise.all([creationRequest(apiBase, "/creations/providers"), creationRequest(apiBase, "/creations/local/setup")]); setProviders(p.items); setLocal(l); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => { void refresh(); }, [apiBase]);
  useEffect(() => { if (!local?.running) return; const timer = setInterval(() => void refresh(), 3000); return () => clearInterval(timer); }, [local?.running, apiBase]);
  const action = async (path: string, body?: unknown) => {
    setBusy(true); setError("");
    try { await creationRequest(apiBase, path, "POST", body); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return <div className="creation-settings">
    <Group title="Image and video creation" description="Ask Echo to create something in chat. Results appear there and in your Creations library. Cloud requests send your prompt to the provider and ask approval before spending credits.">
      <Row label="Enable creation"><Toggle label="Enable creation" checked={Boolean(s.allow_generation_actions)} onChange={value => save({ allow_generation_actions: value })} /></Row>
      {(["image", "video"] as const).map(kind => <React.Fragment key={kind}>
        <Row label={kind === "image" ? "Image provider" : "Video provider"}><Select value={s[`generation_${kind}_provider`] || `gemini-${kind === "image" ? "images" : "video"}`} onChange={value => save({ [`generation_${kind}_provider`]: value, [`generation_${kind}_model`]: "" })} options={[
          { value: kind === "image" ? "gemini-images" : "gemini-video", label: kind === "image" ? "Google Gemini images" : "Google Veo video" },
          ...(kind === "video" ? [{ value: "minimax-video", label: "MiniMax Hailuo video" }] : []),
          { value: "comfyui-local", label: "ComfyUI · local" },
        ]} /></Row>
        <Row label={`${kind === "image" ? "Image" : "Video"} model`} help="Leave empty for the supported default. Local images use an installed checkpoint; local video uses the Wan starter profile."><TextField value={s[`generation_${kind}_model`] || ""} placeholder="Default model" onCommit={value => save({ [`generation_${kind}_model`]: value })} /></Row>
      </React.Fragment>)}
      <Row label="Google API key"><SecretField isSet={Boolean(s.gemini?.api_key)} onCommit={value => save({ gemini: { api_key: value } })} /></Row>
      <Row label="MiniMax API key"><SecretField isSet={Boolean(s.minimax_api_key)} onCommit={value => save({ minimax_api_key: value })} /></Row>
      <Row label="ComfyUI address" help="An existing server on this computer. Managed setup uses port 8188."><TextField value={s.comfyui_base_url || "http://127.0.0.1:8188"} onCommit={value => save({ comfyui_base_url: value })} /></Row>
    </Group>
    <Group title="Provider readiness" description="Configured cloud credentials are checked when you generate. Availability, billing and model access depend on your provider account.">
      {providers.map(p => <Row key={p.id} label={p.id} help={p.detail}><span>{p.execution_ready ? "Configured" : "Needs setup"}</span></Row>)}
      <button className="es-btn es-btn-sm" onClick={() => void refresh()}>Refresh detection</button>
    </Group>
    <Group title="Install local generation" description={local?.detail || "Checking your hardware…"}>
      {local && <>
        <p>{local.hardware?.name || "No supported NVIDIA GPU detected"} · {local.free_gb} GB disk space free</p>
        {local.profiles?.map((p: any) => <Row key={p.id} label={p.label} help={`${p.download_gb} GB download · ${p.recommended_vram_gb} GB VRAM recommended`}>
          <a href={p.license} target="_blank" rel="noreferrer">Model license</a>
          <button className="es-btn es-btn-sm" disabled={busy || local.running || !local.supported} onClick={() => void action("/creations/local/setup", { profile: p.id })}>{p.installed ? "Verify / repair" : "Accept license & install"}</button>
        </Row>)}
        {local.runtime_installed && <button className="es-btn es-btn-sm" disabled={busy || local.running || local.runtime_running} onClick={() => void action("/creations/local/start")}>{local.runtime_running ? "Runtime running" : "Start local runtime"}</button>}
        {local.running && <><progress max={local.total || 1} value={local.downloaded || 0} aria-label="Model download progress" /><button className="es-btn es-btn-sm" onClick={() => void action("/creations/local/cancel")}>Cancel setup</button></>}
        {local.message && <p role="status">{local.message}</p>}
        {local.error && <p role="alert">{local.error}</p>}
      </>}
    </Group>
    {error && <p role="alert">{error}</p>}
  </div>;
}
