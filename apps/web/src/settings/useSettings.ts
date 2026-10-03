import { useCallback, useEffect, useRef, useState } from "react";

export type SettingsMap = Record<string, any>;
export type SaveState = "idle" | "saving" | "saved" | "error";

/** Reads /settings and saves patches immediately (toggles) or on commit (text). */
export function useSettings(apiBase: string) {
  const [settings, setSettings] = useState<SettingsMap | null>(null);
  const [error, setError] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState("");
  const savedTimer = useRef(0);

  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/settings`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSettings(data.settings || {});
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (patch: SettingsMap) => {
      // Optimistic: reflect the change right away, roll back on failure.
      const previous = settings;
      setSettings((current) => mergeDeep(current || {}, patch));
      setSaveState("saving");
      setSaveError("");
      try {
        const resp = await fetch(`${apiBase}/settings`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        });
        if (!resp.ok) {
          const detail = await resp.text();
          throw new Error(detail || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        setSettings(data.settings || {});
        // The chat's model picker listens so a new default shows up there too.
        window.dispatchEvent(new CustomEvent("echospeak:settings-saved", { detail: patch }));
        setSaveState("saved");
        window.clearTimeout(savedTimer.current);
        savedTimer.current = window.setTimeout(() => setSaveState("idle"), 1800);
      } catch (err) {
        setSettings(previous);
        setSaveState("error");
        setSaveError(err instanceof Error ? err.message : String(err));
      }
    },
    [apiBase, settings]
  );

  return { settings, error, reload: load, save, saveState, saveError };
}

export function mergeDeep(base: SettingsMap, patch: SettingsMap): SettingsMap {
  const out: SettingsMap = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === "object" && !Array.isArray(value) && base[key] && typeof base[key] === "object" && !Array.isArray(base[key])) {
      out[key] = mergeDeep(base[key], value);
    } else {
      out[key] = value;
    }
  }
  return out;
}
