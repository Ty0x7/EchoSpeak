import React, { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";

import { EchoFace, echoFaceStyles, type EchoFaceMode } from "./EchoFace";
import { DEFAULT_AVATAR_CONFIG, normalizeAvatarConfig, type AvatarConfig } from "./avatarConfig";
import { notifyAvatarUpdated } from "../dashboard/useAvatarConfig";
export { DEFAULT_AVATAR_CONFIG, type AvatarConfig } from "./avatarConfig";

const PRESETS: Array<{ name: string; config: Partial<AvatarConfig> }> = [
  { name: "Default", config: { body_color: "#ffffff", eye_color: "#000000", glow_color: "#ffffff", bg_color: "#0a0a0a" } },
  { name: "Midnight", config: { body_color: "#8b5cf6", eye_color: "#ddd6fe", glow_color: "#7c3aed", bg_color: "#140b27" } },
  { name: "Ember", config: { body_color: "#fb923c", eye_color: "#fde68a", glow_color: "#ef4444", bg_color: "#190a02" } },
  { name: "Ocean", config: { body_color: "#22d3ee", eye_color: "#cffafe", glow_color: "#0ea5e9", bg_color: "#03151e" } },
  { name: "Forest", config: { body_color: "#4ade80", eye_color: "#dcfce7", glow_color: "#16a34a", bg_color: "#06170d" } },
  { name: "Mono", config: { body_color: "#d4d4d8", eye_color: "#fafafa", glow_color: "#71717a", bg_color: "#0a0a0a" } },
];

type AvatarEditorProps = {
  apiBase: string;
  colors: {
    bg: string;
    panel: string;
    panel2: string;
    accent: string;
    text: string;
    textDim: string;
    line: string;
    danger: string;
  };
  onConfigChange?: (config: AvatarConfig) => void;
};

async function requestJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

export const AvatarEditor: React.FC<AvatarEditorProps> = ({ apiBase, colors, onConfigChange }) => {
  const [preview, setPreview] = useState<EchoFaceMode>("idle");
  const [config, setConfig] = useState<AvatarConfig>(DEFAULT_AVATAR_CONFIG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await requestJson(`${apiBase}/avatar/config`);
      const next = normalizeAvatarConfig(data);
      setConfig(next);
      setDirty(false);
      onConfigChange?.(next);
    } catch (e: any) {
      setError(e.message || "Failed to load avatar config");
    } finally {
      setLoading(false);
    }
  }, [apiBase, onConfigChange]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const updateField = <K extends keyof AvatarConfig>(key: K, value: AvatarConfig[K]) => {
    setConfig((prev) => {
      const next = { ...prev, [key]: value };
      onConfigChange?.(next);
      return next;
    });
    setDirty(true);
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const data = await requestJson(`${apiBase}/avatar/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const next = normalizeAvatarConfig(data);
      setConfig(next);
      setDirty(false);
      setSaved(true);
      notifyAvatarUpdated();
      onConfigChange?.(next);
      window.setTimeout(() => setSaved(false), 1800);
    } catch (e: any) {
      setError(e.message || "Failed to save avatar config");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setError(null);
    try {
      const data = await requestJson(`${apiBase}/avatar/config/reset`, { method: "POST" });
      const next = normalizeAvatarConfig(data);
      setConfig(next);
      setDirty(false);
      setSaved(false);
      notifyAvatarUpdated();
      onConfigChange?.(next);
    } catch (e: any) {
      setError(e.message || "Failed to reset avatar config");
    }
  };

  const applyPreset = (preset: Partial<AvatarConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...preset };
      onConfigChange?.(next);
      return next;
    });
    setDirty(true);
    setSaved(false);
  };

  // Theme surfaces, so the editor matches the rest of Settings in light and dark.
  const cardStyle: React.CSSProperties = {
    background: "var(--es-surface-1)",
    border: "1px solid var(--es-border)",
    borderRadius: 12,
    padding: 16,
    boxShadow: "0 8px 24px -18px rgba(var(--es-shade-rgb), calc(0.9 * var(--es-shade-k)))",
  };

  const labelStyle: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 700,
    color: colors.textDim,
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 10,
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--es-surface-2)",
    border: "1px solid var(--es-border)",
    borderRadius: 10,
    padding: "10px 12px",
    color: colors.text,
    fontSize: 12,
    outline: "none",
  };

  if (loading) {
    return (
      <div className="research-scroll">
        <div className="research-card" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 240, color: colors.textDim }}>
          Loading avatar settings...
        </div>
      </div>
    );
  }

  return (
    <div className="research-scroll">
      <div className="research-card" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 4 }}>
          {dirty ? <span style={{ fontSize: 11, color: "#f59e0b" }}>Unsaved</span> : null}
          {saved ? <span style={{ fontSize: 11, color: "#22c55e" }}>Saved</span> : null}
          <button className="icon-button" type="button" onClick={save} disabled={saving || !dirty} style={{ height: 34, padding: "0 14px", fontSize: 12, opacity: dirty ? 1 : 0.55 }}>
            {saving ? "Saving..." : "Save"}
          </button>
          <button className="icon-button" type="button" onClick={reset} style={{ height: 34, padding: "0 14px", fontSize: 12 }}>
            Reset
          </button>
        </div>

        {error ? (
          <div style={{ color: colors.danger, padding: 12, borderRadius: 12, border: "1px solid rgba(214, 60, 60, 0.2)", background: "rgba(239,68,68,0.08)", fontSize: 12 }}>
            {error}
          </div>
        ) : null}

        <div className="avatar-settings-grid">
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={cardStyle}>
              <div style={labelStyle}>Presets</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {PRESETS.map((preset) => (
                  <button key={preset.name} type="button" onClick={() => applyPreset(preset.config)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderRadius: 999, border: "1px solid var(--es-border)", background: "var(--es-surface-2)", color: colors.text, cursor: "pointer", fontSize: 12 }}>
                    <span style={{ width: 10, height: 10, borderRadius: "50%", background: preset.config.body_color || "#fff", boxShadow: `0 0 0 2px ${(preset.config.glow_color || "#fff")}33` }} />
                    {preset.name}
                  </button>
                ))}
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Appearance</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 12 }}>
                {([
                  ["Body", "body_color"],
                  ["Eyes", "eye_color"],
                  ["Glow", "glow_color"],
                  ["Backdrop", "bg_color"],
                ] as const).map(([label, field]) => (
                  <div key={field} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <span style={{ fontSize: 12, color: colors.textDim }}>{label}</span>
                    <div style={{ display: "flex", gap: 8 }}>
                      <input type="color" value={config[field]} onChange={(e) => updateField(field, e.target.value)} style={{ width: 44, height: 36, borderRadius: 10, border: `1px solid ${colors.line}`, background: "transparent", padding: 0 }} />
                      <input value={config[field]} onChange={(e) => updateField(field, e.target.value)} style={{ ...inputStyle, fontFamily: "monospace" }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Motion</div>
              <div style={{ display: "grid", gap: 14 }}>
                {([
                  ["Eye Size", "eye_size", 0.5, 2, 0.1, "x"],
                  ["Roundness", "body_roundness", 4, 40, 1, "px"],
                  ["Breathing", "breathing_speed", 0.4, 2.6, 0.1, "x"],
                  ["Voice avatar", "voice_avatar_scale", 0.7, 1.2, 0.1, "x"],
                ] as const).map(([label, field, min, max, step, unit]) => (
                  <div key={field} style={{ display: "grid", gridTemplateColumns: "110px 1fr 52px", alignItems: "center", gap: 12 }}>
                    <span style={{ fontSize: 12, color: colors.textDim }}>{label}</span>
                    <input type="range" min={min} max={max} step={step} value={config[field]} onChange={(e) => updateField(field, parseFloat(e.target.value) as never)} style={{ width: "100%", accentColor: config.glow_color }} />
                    <span style={{ fontSize: 11, color: colors.text, fontFamily: "monospace", textAlign: "right" }}>{config[field].toFixed(step < 1 ? 1 : 0)}{unit}</span>
                  </div>
                ))}
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Behavior</div>
              <div style={{ display: "grid", gap: 14 }}>
                <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 12, alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: colors.textDim }}>Idle Mode</span>
                  <select value={["auto", "breathe", "none"].includes(config.idle_activity) ? config.idle_activity : "auto"} onChange={(e) => updateField("idle_activity", e.target.value)} style={{ ...inputStyle, paddingRight: 32 }}>
                    <option value="auto">Follow pointer & look around</option>
                    <option value="breathe">Gentle float</option>
                    <option value="none">Still</option>
                  </select>
                </div>
                {([
                  ["Glow", "enable_glow"],
                  ["Idle Activities", "enable_idle_activities"],
                ] as const).map(([label, field]) => (
                  <div key={field} style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ fontSize: 12, color: colors.textDim }}>{label}</span>
                    <button type="button" onClick={() => updateField(field, (!config[field]) as never)} style={{ width: 42, height: 24, borderRadius: 999, border: "none", background: config[field] ? "var(--es-accent)" : "var(--es-glass-2)", cursor: "pointer", position: "relative" }}>
                      <motion.div animate={{ x: config[field] ? 20 : 2 }} transition={{ duration: 0.16 }} style={{ position: "absolute", top: 2, width: 20, height: 20, borderRadius: "50%", background: config[field] ? "#ffffff" : colors.textDim }} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="avatar-preview-card" style={{ ...cardStyle, padding: 18 }}>
              <div style={labelStyle}>Echo in voice & companion</div>
              <div className="avatar-preview-states" role="group" aria-label="Preview avatar state">
                {["idle", "listening", "thinking", "working", "speaking", "error"].map(state => <button type="button" key={state} aria-pressed={preview === state} onClick={() => setPreview(state as EchoFaceMode)}>{state}</button>)}
              </div>
              <div className="avatar-live-preview" style={{ transform: `scale(${config.voice_avatar_scale})` }}>
                <style>{echoFaceStyles}</style><EchoFace size={140} avatarConfig={config} mode={preview} aura level={preview === "listening" ? 0.5 : 0} />
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Status Label</div>
              <input value={config.custom_status_text} onChange={(e) => updateField("custom_status_text", e.target.value)} placeholder="Optional status line under the avatar" style={inputStyle} />
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>One Echo, wherever you talk</div>
              <div style={{ display: "grid", gap: 8, fontSize: 12, color: colors.textDim, lineHeight: 1.55 }}>
                <div>Preview the same Echo you see during voice conversations and in your desktop companion.</div>
                <div>Save to use this appearance in both places. Voice avatar size adjusts Echo in the conversation view.</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
