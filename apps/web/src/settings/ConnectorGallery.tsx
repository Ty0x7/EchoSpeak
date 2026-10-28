import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrandMark } from "../components/BrandMark";
import { Group, Status } from "./controls";

type Setting = { name: string; about?: string; secret?: boolean; required?: boolean; default?: string };
export type Connector = {
  id: string; app: string; field: string; category: string; publisher: string; kind: string; sign_in: boolean; where: string;
  needs: string[]; missing: string[]; steps: string[]; watch_out: string[]; settings: Setting[]; homepage: string;
  server: string; state: "not_connected" | "connected" | "starting" | "sign_in_needed" | "signing_in" | "problem" | "builtin";
  tool_count: number; problem: string;
};

const STATE_LABEL: Record<Connector["state"], [string, "ok" | "warn" | "err" | "idle"]> = {
  connected: ["Connected", "ok"], starting: ["Starting…", "idle"], signing_in: ["Signing in…", "idle"],
  sign_in_needed: ["Sign-in needed", "warn"], problem: ["Needs attention", "err"], not_connected: ["", "idle"], builtin: ["Built in", "idle"],
};

/** "Official" when the app's own maker publishes the server; anything else is community-made. */
export const isOfficial = (connector: Pick<Connector, "publisher">) => connector.publisher.toLowerCase().startsWith("official") || connector.publisher.startsWith("built into");

/** Connectors matching a search, in gallery order: connected first, then by name. */
export function filterConnectors(items: Connector[], search: string, category: string): Connector[] {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  return items
    .filter(item => (!category || item.category === category)
      && words.every(word => `${item.app} ${item.field} ${item.category}`.toLowerCase().includes(word)))
    .sort((a, b) => Number(b.state !== "not_connected" && b.state !== "builtin") - Number(a.state !== "not_connected" && a.state !== "builtin") || a.app.localeCompare(b.app));
}

async function post<T>(url: string, body: unknown = {}): Promise<T> {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(data?.detail || `Request failed (${response.status})`));
  return data as T;
}

/** Settings › Connections: add apps in one click, the reviewed way. */
export function ConnectorGallery({ apiBase }: { apiBase: string }) {
  const [items, setItems] = useState<Connector[] | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [open, setOpen] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<Record<string, string>>({});
  const polling = useRef(0);

  const load = useCallback(async () => {
    const response = await fetch(`${apiBase}/lean/integrations/gallery`);
    if (response.ok) setItems(((await response.json()).items || []) as Connector[]);
  }, [apiBase]);
  useEffect(() => { void load(); }, [load]);
  // While something is starting or signing in, follow it until it settles (about two minutes at most).
  useEffect(() => {
    if (!items?.some(item => item.state === "signing_in" || item.state === "starting") || polling.current > 60) { polling.current = 0; return; }
    const timer = window.setTimeout(() => { polling.current += 1; void load(); }, 2000);
    return () => window.clearTimeout(timer);
  }, [items, load]);

  const categories = useMemo(() => [...new Set((items || []).map(item => item.category))], [items]);
  const shown = filterConnectors(items || [], search, category);
  const act = async (item: Connector, work: () => Promise<unknown>) => {
    setBusy(item.id); setError(e => ({ ...e, [item.id]: "" }));
    try { await work(); setOpen(""); setValues({}); await load(); }
    catch (err) { setError(e => ({ ...e, [item.id]: err instanceof Error ? err.message : "Request failed" })); }
    finally { setBusy(""); }
  };
  const connect = (item: Connector) => act(item, () => post(`${apiBase}/lean/integrations/gallery/${item.id}/connect`, { values }));
  const signIn = (item: Connector) => act(item, () => post(`${apiBase}/lean/integrations/gallery/${item.id}/signin`));
  const disconnect = (item: Connector) => window.confirm(`Disconnect ${item.app}? EchoSpeak signs out and forgets it.`)
    && act(item, () => post(`${apiBase}/lean/integrations/gallery/${item.id}/disconnect`));
  const connected = (items || []).filter(item => !["not_connected", "builtin"].includes(item.state)).length;

  return (
    <Group title="Add apps" description={`One click: EchoSpeak sets each app up the reviewed way and opens the app's own sign-in when it has one. ${items ? `${connected} of ${items.length} connected.` : ""}`}>
      <div className="st-gallery-tools">
        <input aria-label="Search apps" placeholder="Search apps, for example tasks, design, database…" value={search} onChange={event => setSearch(event.target.value)} />
        <div className="st-gallery-chips" role="group" aria-label="Kinds of apps">
          <button type="button" aria-pressed={!category} onClick={() => setCategory("")}>All</button>
          {categories.map(name => <button key={name} type="button" aria-pressed={category === name} onClick={() => setCategory(category === name ? "" : name)}>{name}</button>)}
        </div>
      </div>
      {items === null ? <p className="st-muted">Loading apps…</p> : !shown.length ? <p className="st-muted">No app matches. Agents can also find one for you: ask Echo to connect it.</p> : null}
      <div className="st-gallery" role="list">
        {shown.map(item => {
          const [label, tone] = STATE_LABEL[item.state];
          const isOpen = open === item.id;
          return (
            <div key={item.id} role="listitem" className={`st-gallery-card${isOpen ? " is-open" : ""}`}>
              <div className="st-gallery-head">
                <BrandMark id={item.id} name={item.app} size={26} />
                <span className="st-gallery-name"><strong>{item.app}</strong><small>{item.field}</small></span>
                <span className={`st-gallery-badge${isOfficial(item) ? " is-official" : ""}`}>{isOfficial(item) ? "Official" : "Community"}</span>
              </div>
              <div className="st-gallery-foot">
                {label ? <Status tone={tone}>{label}{item.state === "connected" && item.tool_count ? ` · ${item.tool_count} tools` : ""}</Status> : <span />}
                {item.state === "builtin" ? <small className="st-muted">{item.where}</small>
                  : item.state === "not_connected" ? <button type="button" className="es-btn es-btn-sm" disabled={Boolean(busy)} aria-expanded={isOpen}
                      onClick={() => { setOpen(isOpen ? "" : item.id); setValues({}); }}>{isOpen ? "Cancel" : "Connect"}</button>
                  : <span className="st-gallery-actions">
                      {item.state === "sign_in_needed" ? <button type="button" className="es-btn es-btn-sm" disabled={Boolean(busy)} onClick={() => void signIn(item)}>Sign in</button> : null}
                      <button type="button" className="es-btn es-btn-sm es-btn-quiet" disabled={Boolean(busy)} onClick={() => void disconnect(item)}>Disconnect</button>
                    </span>}
              </div>
              {item.state === "problem" && item.problem ? <small className="st-gallery-problem">{item.problem}</small> : null}
              {error[item.id] ? <small className="st-gallery-problem" role="alert">{error[item.id]}</small> : null}
              {isOpen ? (
                <form className="st-gallery-setup" onSubmit={event => { event.preventDefault(); void connect(item); }}>
                  {item.missing.length ? <Status tone="warn">Needs {item.missing.join(", ")} on this PC first.</Status> : null}
                  {item.steps.length ? <ol>{item.steps.map(step => <li key={step}>{step}</li>)}</ol> : null}
                  {item.settings.filter(s => s.required || !s.default).map(setting => (
                    <label key={setting.name}>{setting.name}{setting.required ? "" : " (optional)"}
                      <input type={setting.secret ? "password" : "text"} autoComplete="off" required={Boolean(setting.required)} value={values[setting.name] || ""}
                        onChange={event => setValues(v => ({ ...v, [setting.name]: event.target.value }))} />
                      {setting.about ? <small>{setting.about}</small> : null}</label>
                  ))}
                  {item.watch_out.length ? <ul className="st-gallery-watch">{item.watch_out.map(note => <li key={note}>{note}</li>)}</ul> : null}
                  <div className="st-gallery-confirm">
                    <button type="submit" className="es-btn es-btn-sm es-btn-primary" disabled={busy === item.id || item.missing.length > 0}>
                      {busy === item.id ? "Connecting…" : item.sign_in ? `Connect and sign in to ${item.app}` : `Connect ${item.app}`}</button>
                    {item.homepage ? <a href={item.homepage} target="_blank" rel="noopener noreferrer">Details ↗</a> : null}
                  </div>
                  <small className="st-muted">By {item.publisher}. Agents ask you before anything that changes data in {item.app}.</small>
                </form>
              ) : null}
            </div>
          );
        })}
      </div>
    </Group>
  );
}
