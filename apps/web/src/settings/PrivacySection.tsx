import "./systemcheck.css";
import React, { useCallback, useEffect, useState } from "react";
import { ChoiceCards, Group, ListEditor, Row, Segmented } from "./controls";
import type { SettingsMap } from "./useSettings";

/**
 * Settings › Privacy (backend: agent/privacy.py, agent/privacy_check.py).
 * The mode, what each part of EchoSpeak sends and where, a check that probes this
 * setup, and the honest list of what the mode can't control.
 */

type Component = {
  id: string; label: string; sends: string; content: boolean; note: string;
  state: "allowed" | "local_only"; override: string; allowed: number; blocked: number; hosts: string[];
};
type Status = {
  mode: "standard" | "private" | "offline";
  components: Component[];
  other: { allowed: number; blocked: number; hosts: string[] };
  backstop: boolean;
  not_enforced: string[];
};
type CheckItem = { id: string; label: string; status: "ok" | "warn" | "blocked" | "info"; detail: string; fix: string };
type Check = { mode: string; verdict: string; items: CheckItem[] };
type LogEntry = { at: number; component: string; host: string; allowed: boolean; via: string };

const MODES = [
  { value: "standard", title: "Standard", body: "Everything works as you set it up. Echo shows what talks to the internet." },
  { value: "private", title: "Private", badge: "Your content stays home",
    body: "Your messages, files, memories and searches never go to a service outside your machines. Local models and your own SearXNG keep working." },
  { value: "offline", title: "Offline",
    body: "Only this PC and your own network. Nothing reaches the internet: no web pages, downloads or update checks." },
];

const CHECK_LABEL: Record<CheckItem["status"], string> = {
  ok: "Working", warn: "Needs attention", blocked: "Off in this mode", info: "Reaches outside",
};

export function PrivacySection({ s, save, apiBase }: { s: SettingsMap; save(patch: SettingsMap): Promise<void>; apiBase: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mode = String(s.privacy_mode || "standard");
  const overrides: Record<string, string> = (s.privacy_overrides && typeof s.privacy_overrides === "object") ? s.privacy_overrides : {};
  const trusted: string[] = Array.isArray(s.privacy_trusted_hosts) ? s.privacy_trusted_hosts : [];

  const load = useCallback(async () => {
    try {
      const [st, lg] = await Promise.all([
        fetch(`${apiBase}/privacy/status`, { cache: "no-store" }).then((r) => r.json()),
        fetch(`${apiBase}/privacy/log?limit=30`, { cache: "no-store" }).then((r) => r.json()),
      ]);
      setStatus(st as Status);
      setLog(Array.isArray(lg?.items) ? lg.items : []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase]);
  useEffect(() => { void load(); }, [load, mode]);

  const runCheck = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${apiBase}/privacy/check`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCheck((await res.json()) as Check);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { setCheck(null); }, [mode]);

  const setOverride = (id: string, value: string) => {
    const next = { ...overrides };
    if (value === "default") delete next[id]; else next[id] = value;
    void save({ privacy_overrides: next });
  };

  return (
    <div className="st-section">
      <Group title="Privacy mode" description="Enforced, not just a setting: each part of EchoSpeak asks before it connects, and a backstop refuses any internet connection nothing approved.">
        <ChoiceCards value={mode} options={MODES} onChange={(v) => void save({ privacy_mode: v })} />
      </Group>

      <Group title="Check this setup" description="Probes what this setup really does: does your local model answer, does your SearXNG return results, and what would still leave your machines."
        action={<button type="button" className="es-btn" disabled={busy} onClick={() => void runCheck()}>{busy ? "Checking…" : check ? "Check again" : "Run the check"}</button>}>
        {check ? (
          <>
            <p className="st-syscheck-summary">{check.verdict}</p>
            <ul className="st-syscheck st-privacy-check">
              {check.items.map((item) => (
                <li key={item.id} data-status={item.status === "blocked" ? "off" : item.status === "info" && mode !== "standard" ? "warn" : item.status === "info" ? "ok" : item.status}>
                  <span className="st-syscheck-dot" aria-hidden />
                  <div>
                    <b>{item.label}</b>
                    <span className="st-syscheck-status">{CHECK_LABEL[item.status] || item.status}</span>
                    <p>{item.detail}</p>
                    {item.fix && item.status !== "ok" ? <p className="st-syscheck-fix">{item.fix}</p> : null}
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : <Row label={<span className="st-muted">Not run yet.</span>} />}
        {error ? <p className="st-syscheck-error">Couldn't load privacy details: {error}</p> : null}
      </Group>

      <Group title="What talks to the internet" description="Each part, what it sends, and what happened since EchoSpeak started. Counts stay in memory and are never saved.">
        {(status?.components || []).map((c) => (
          <Row key={c.id}
            label={<span><b>{c.label}</b> · <span className={c.state === "allowed" ? "st-privacy-on" : "st-privacy-off"}>{c.state === "allowed" ? "Allowed" : "Your machines only"}</span></span>}
            help={<>
              <span>Sends: {c.sends}.</span>
              {c.note ? <span> {c.note}</span> : null}
              {c.allowed || c.blocked ? <span> {c.allowed} connection{c.allowed === 1 ? "" : "s"}{c.blocked ? `, ${c.blocked} refused` : ""}{c.hosts.length ? ` (${c.hosts.slice(0, 4).join(", ")}${c.hosts.length > 4 ? "…" : ""})` : ""}.</span> : null}
            </>}>
            {mode === "offline" ? null : (
              <Segmented value={overrides[c.id] || "default"} onChange={(v) => setOverride(c.id, v)}
                options={[{ value: "default", label: "Default" }, { value: "allow", label: "Allow" }, { value: "block", label: "Off" }]} />
            )}
          </Row>
        ))}
        {status && (status.other.allowed || status.other.blocked) ? (
          <Row label="Other connections" help={`${status.other.allowed} allowed, ${status.other.blocked} refused${status.other.hosts.length ? `: ${status.other.hosts.slice(0, 6).join(", ")}` : ""}.`} />
        ) : null}
      </Group>

      <Group title="Hosts you trust" description="Servers that count as yours, such as your SearXNG on a VPS or a model server over your VPN. Use *.example.com for a whole domain.">
        <ListEditor items={trusted} onChange={(items) => void save({ privacy_trusted_hosts: items })} placeholder="search.example.com" mono />
      </Group>

      <Group title="What a privacy mode can't control" description="Said plainly, so the mode never promises more than it does.">
        <ul className="st-privacy-limits">
          {(status?.not_enforced || []).map((line) => <li key={line}>{line}</li>)}
          <li>{status?.backstop ? "The backstop is active in this process." : "The backstop isn't active in this process yet; restart EchoSpeak."}</li>
        </ul>
      </Group>

      <Group title="Recent connections" description="The last few, newest first. Host names only: no addresses, searches or content."
        action={<button type="button" className="es-btn es-btn-sm" onClick={() => void fetch(`${apiBase}/privacy/log/clear`, { method: "POST" }).then(load)}>Clear</button>}>
        {log.length ? (
          <ul className="st-privacy-log">
            {log.map((entry, index) => (
              <li key={`${entry.at}-${index}`} data-allowed={entry.allowed ? "yes" : "no"}>
                <time>{new Date(entry.at * 1000).toLocaleTimeString()}</time>
                <span className="is-mono">{entry.host || "—"}</span>
                <span>{entry.component.replace(/_/g, " ")}</span>
                <span>{entry.allowed ? "allowed" : "refused"}</span>
              </li>
            ))}
          </ul>
        ) : <Row label={<span className="st-muted">Nothing yet.</span>} />}
      </Group>
    </div>
  );
}
