import "./systemcheck.css";
import React, { useCallback, useEffect, useState } from "react";

/**
 * System check (backend: agent/health.py, GET /health/capabilities).
 * Settings › System check lists every check; the chat shows a notice at startup
 * when something has failed, so a broken web search is found before a chat
 * depends on it.
 */

export type HealthItem = { id: string; label: string; status: "ok" | "warn" | "fail"; detail: string; fix: string };

export function useCapabilities(apiBase: string) {
  const [items, setItems] = useState<HealthItem[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    try {
      const res = await fetch(`${apiBase}/health/capabilities${refresh ? "?refresh=true" : ""}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { items?: HealthItem[] };
      setItems(Array.isArray(data.items) ? data.items : []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [apiBase]);
  useEffect(() => { void load(); }, [load]);
  return { items, error, busy, reload: load };
}

const STATUS_LABEL: Record<HealthItem["status"], string> = { ok: "Working", warn: "Limited", fail: "Not working" };

export function SystemCheckSection({ apiBase }: { apiBase: string }) {
  const { items, error, busy, reload } = useCapabilities(apiBase);
  const problems = (items || []).filter((item) => item.status !== "ok").length;
  return (
    <div className="st-section">
      <div className="st-syscheck-head">
        <p className="st-syscheck-summary">
          {items === null ? "Checking…" : problems ? `${problems} thing${problems === 1 ? "" : "s"} need${problems === 1 ? "s" : ""} attention.` : "Everything is working."}
        </p>
        <button type="button" className="es-btn" disabled={busy} onClick={() => void reload(true)}>{busy ? "Checking…" : "Check again"}</button>
      </div>
      {error ? <p className="st-syscheck-error">Couldn't run the check: {error}</p> : null}
      <ul className="st-syscheck">
        {(items || []).map((item) => (
          <li key={item.id} data-status={item.status}>
            <span className="st-syscheck-dot" aria-hidden />
            <div>
              <b>{item.label}</b>
              <span className="st-syscheck-status">{STATUS_LABEL[item.status] || item.status}</span>
              <p>{item.detail}</p>
              {item.fix && item.status !== "ok" ? <p className="st-syscheck-fix">{item.fix}</p> : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const DISMISS_KEY = "echospeak.syscheck.dismissed";

/** A one-line notice above the chat when a check has failed. Dismissed until the problem changes. */
export function SystemCheckNotice({ apiBase, onOpen }: { apiBase: string; onOpen(): void }) {
  const { items } = useCapabilities(apiBase);
  const failed = (items || []).filter((item) => item.status === "fail" && item.id !== "model");
  const signature = failed.map((item) => `${item.id}:${item.detail}`).join("|");
  const [dismissed, setDismissed] = useState(() => {
    try { return sessionStorage.getItem(DISMISS_KEY) || ""; } catch { return ""; }
  });
  if (!failed.length || dismissed === signature) return null;
  const first = failed[0];
  const dismiss = () => {
    setDismissed(signature);
    try { sessionStorage.setItem(DISMISS_KEY, signature); } catch { /* private mode */ }
  };
  return (
    <div className="syscheck-notice" role="status">
      <span className="syscheck-notice-dot" aria-hidden />
      <span className="syscheck-notice-text">
        <b>{first.label}: not working.</b> {first.fix}
        {failed.length > 1 ? ` (+${failed.length - 1} more)` : ""}
      </span>
      <button type="button" className="es-btn es-btn-quiet" onClick={onOpen}>Details</button>
      <button type="button" className="syscheck-notice-close" onClick={dismiss} aria-label="Dismiss">×</button>
    </div>
  );
}
