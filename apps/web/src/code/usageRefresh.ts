/** Codex sends limit events; this is Echo's five-minute fallback, not a provider mandate. */
export const USAGE_REFRESH_MS = 5 * 60 * 1000;

export function startUsageRefresh(refresh: () => Promise<unknown>, onError: (error: unknown) => void,
  target: Pick<Window, "addEventListener" | "removeEventListener"> = window) {
  let live = true;
  let pending = false;
  let attempted = Date.now();
  let timer: ReturnType<typeof setTimeout>;
  const update = async () => {
    if (!live || pending) return;
    clearTimeout(timer);
    attempted = Date.now(); pending = true;
    try { await refresh(); }
    catch (error) { if (live) onError(error); }
    finally { pending = false; if (live) timer = setTimeout(update, USAGE_REFRESH_MS); }
  };
  const focus = () => { if (Date.now() - attempted >= USAGE_REFRESH_MS) void update(); };
  timer = setTimeout(update, USAGE_REFRESH_MS);
  target.addEventListener("focus", focus);
  return () => { live = false; clearTimeout(timer); target.removeEventListener("focus", focus); };
}
