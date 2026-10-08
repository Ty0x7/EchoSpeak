import React, { useCallback, useEffect, useState } from "react";
import "./stopeverything.css";

/**
 * Stop everything (backend: agent/lean/stop.py). One button that cancels every
 * running agent and pauses routines, channels and inbound A2A until Resume.
 * Two clicks, so it can't go off by accident. While paused, a notice above the
 * chat says so and offers Resume.
 */

type StopStatus = { paused: boolean; since: number; running: number };

const listeners = new Set<(s: StopStatus) => void>();
let last: StopStatus = { paused: false, since: 0, running: 0 };

async function fetchStatus(apiBase: string): Promise<StopStatus> {
  const res = await fetch(`${apiBase}/lean/stop-status`, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = (await res.json()) as Partial<StopStatus>;
  return { paused: Boolean(data.paused), since: Number(data.since || 0), running: Number(data.running || 0) };
}

function publish(status: StopStatus) {
  last = status;
  listeners.forEach((fn) => fn(status));
}

function useStopStatus(apiBase: string) {
  const [status, setStatus] = useState<StopStatus>(last);
  useEffect(() => {
    listeners.add(setStatus);
    let alive = true;
    const poll = () => fetchStatus(apiBase).then((s) => alive && publish(s)).catch(() => undefined);
    void poll();
    const timer = window.setInterval(poll, 15000);
    return () => { alive = false; listeners.delete(setStatus); window.clearInterval(timer); };
  }, [apiBase]);
  const post = useCallback(async (path: "stop-all" | "resume") => {
    const res = await fetch(`${apiBase}/lean/${path}`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    publish(await fetchStatus(apiBase));
  }, [apiBase]);
  return { status, post };
}

export function StopEverythingButton({ apiBase }: { apiBase: string }) {
  const { status, post } = useStopStatus(apiBase);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(timer);
  }, [armed]);
  if (status.paused) return null;
  const click = async () => {
    if (!armed) { setArmed(true); return; }
    setBusy(true);
    try { await post("stop-all"); } catch { /* the notice shows the state */ } finally { setBusy(false); setArmed(false); }
  };
  return (
    <button type="button" className={`stop-all${armed ? " is-armed" : ""}`} disabled={busy} onClick={() => void click()}
      title="Cancel every running agent and pause routines, channels and A2A">
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><rect x="1.5" y="1.5" width="9" height="9" rx="2" fill="currentColor" /></svg>
      {busy ? "Stopping…" : armed ? "Click again to stop all agents" : "Stop everything"}
    </button>
  );
}

export function PausedNotice({ apiBase }: { apiBase: string }) {
  const { status, post } = useStopStatus(apiBase);
  const [busy, setBusy] = useState(false);
  if (!status.paused) return null;
  return (
    <div className="paused-notice" role="status">
      <span className="paused-notice-dot" aria-hidden />
      <span className="paused-notice-text">
        <b>All agents are paused.</b> Routines, channels and outside agents won't run until you resume. Your own chats still work.
      </span>
      <button type="button" className="es-btn es-btn-primary" disabled={busy}
        onClick={() => { setBusy(true); void post("resume").catch(() => undefined).finally(() => setBusy(false)); }}>
        {busy ? "Resuming…" : "Resume"}
      </button>
    </div>
  );
}
