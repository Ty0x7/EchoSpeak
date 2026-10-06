import { useEffect, useState } from "react";
import { DEFAULT_AVATAR_CONFIG, normalizeAvatarConfig, type AvatarConfig } from "../components/avatarConfig";

export const AVATAR_UPDATED = "echospeak.avatar.updated";
export function notifyAvatarUpdated() {
  window.localStorage.setItem(AVATAR_UPDATED, String(Date.now()));
  window.dispatchEvent(new Event(AVATAR_UPDATED));
}

/** The same saved avatar in Settings, Voice and the floating companion. */
export function useAvatarConfig(apiBase: string, enabled = true) {
  const [config, setConfig] = useState<AvatarConfig>(DEFAULT_AVATAR_CONFIG);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const refresh = () => { void fetch(`${apiBase}/avatar/config`).then(r => r.ok ? r.json() : null).then(value => { if (alive && value) setConfig(normalizeAvatarConfig(value)); }).catch(() => undefined); };
    const storage = (event: StorageEvent) => { if (event.key === AVATAR_UPDATED) refresh(); };
    refresh();
    window.addEventListener(AVATAR_UPDATED, refresh);
    window.addEventListener("focus", refresh);
    window.addEventListener("storage", storage);
    return () => { alive = false; window.removeEventListener(AVATAR_UPDATED, refresh); window.removeEventListener("focus", refresh); window.removeEventListener("storage", storage); };
  }, [apiBase, enabled]);
  return config;
}
