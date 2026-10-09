import { getEchoSpeakApiBase } from "./bridge";

/**
 * The desktop update check runs in the app shell, outside the backend's privacy backstop,
 * so it asks the backend first (agent/privacy.py `updates_allowed`). Offline mode, or the
 * update check switched off in Settings › Privacy, means no check.
 *
 * If the backend can't answer, the last known answer is used, and an unknown answer allows
 * the check: updates are how a broken install gets fixed.
 */
const KEY = "echospeak.privacy.updatesAllowed";

export async function updatesAllowed(): Promise<boolean> {
  try {
    const res = await fetch(`${getEchoSpeakApiBase()}/privacy/status`, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    const allowed = Boolean((await res.json())?.updates_allowed ?? true);
    try { localStorage.setItem(KEY, allowed ? "1" : "0"); } catch { /* storage may be unavailable */ }
    return allowed;
  } catch {
    try { return localStorage.getItem(KEY) !== "0"; } catch { return true; }
  }
}

export const UPDATES_OFF_MESSAGE = "Update checks are off in your privacy settings (Settings › Privacy).";
