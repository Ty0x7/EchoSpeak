import { useEffect, useState } from "react";
import { checkForDesktopUpdate, type DesktopUpdateInfo } from "../desktop/bridge";

export const UPDATE_CHECKED = "echospeak.update.checked";
let latest: DesktopUpdateInfo | null = null;
export function announceUpdate(info: DesktopUpdateInfo) {
  latest = info;
  window.dispatchEvent(new CustomEvent(UPDATE_CHECKED, { detail: info }));
}
export function useDesktopUpdate(enabled: boolean) {
  const [info, setInfo] = useState(latest);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let checkedAt = 0;
    let checking = false;
    const check = async () => {
      if (checking || Date.now() - checkedAt < 60 * 60 * 1000) return;
      checking = true; checkedAt = Date.now();
      try { const result = await checkForDesktopUpdate(); if (alive) announceUpdate(result); }
      catch { /* An offline update check never blocks startup or claims an update exists. */ }
      finally { checking = false; }
    };
    const updated = (event: Event) => setInfo((event as CustomEvent<DesktopUpdateInfo>).detail);
    window.addEventListener(UPDATE_CHECKED, updated);
    window.addEventListener("focus", check);
    const timer = window.setInterval(check, 6 * 60 * 60 * 1000);
    void check();
    return () => { alive = false; window.clearInterval(timer); window.removeEventListener("focus", check); window.removeEventListener(UPDATE_CHECKED, updated); };
  }, [enabled]);
  return info;
}
