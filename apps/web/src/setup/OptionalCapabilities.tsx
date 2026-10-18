import React, { useEffect, useState } from "react";
import { Group, Row } from "../settings/controls";
import type { SettingsMap } from "../settings/useSettings";
import { VoiceSection } from "../settings/SettingsPanel";
import { GenerationSettings } from "../creations/GenerationSettings";
import { SPEECH_PROVIDERS } from "../settings/VoiceProviders";

type Save = (patch: Record<string, unknown>) => Promise<void>;

/** Optional setup stays optional; reuse the same provider controls as Settings. */
export function OptionalCapabilities({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const [area, setArea] = useState<"" | "voice" | "creations">("");
  return <>
    <Group title="Choose what to set up" description="You can start chatting without voice or media models. Nothing is installed or loaded by opening this page.">
      <Row label="Voice" help="Use Windows voices, Piper, ElevenLabs or optional Drama-3 speech. Listening has its own small local model.">
        <button className="es-btn es-btn-sm" aria-pressed={area === "voice"} onClick={() => setArea(area === "voice" ? "" : "voice")}>{area === "voice" ? "Hide voice setup" : "Set up voice"}</button>
      </Row>
      <Row label="Images and videos" help="Configure your existing local or cloud creation provider when you need it.">
        <button className="es-btn es-btn-sm" aria-pressed={area === "creations"} onClick={() => setArea(area === "creations" ? "" : "creations")}>{area === "creations" ? "Hide creation setup" : "Set up creations"}</button>
      </Row>
    </Group>
    {area === "voice" ? <VoiceSection s={s} save={save} apiBase={apiBase} openAdvanced={() => undefined} /> : null}
    {area === "creations" ? <GenerationSettings s={s} save={save} apiBase={apiBase} /> : null}
  </>;
}

export function SetupVoiceSummary({ s, apiBase, onConfigure }: { s: SettingsMap; apiBase: string; onConfigure(): void }) {
  const selected = String(s.voice_local_tts_provider || "windows-sapi");
  const [detail, setDetail] = useState("Voice is optional. You can configure it after your first chat.");
  useEffect(() => {
    let active = true;
    void fetch(`${apiBase}/media-runtime/voice/providers`).then(response => response.ok ? response.json() : null).then(data => {
      const provider = data?.items?.find((item: { id: string }) => item.id === selected);
      if (active && provider) setDetail(provider.detail);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [apiBase, selected]);
  return <div className="setup-voice-summary"><div><strong>{SPEECH_PROVIDERS.find(p => p.value === selected)?.label || selected}</strong><p>{detail}</p></div>
    <button type="button" className="es-btn es-btn-sm" onClick={onConfigure}>Voice setup</button>
  </div>;
}
