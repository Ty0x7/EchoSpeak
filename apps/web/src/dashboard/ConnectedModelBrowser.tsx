import React, { useEffect, useId, useState } from "react";
import { fallbackProviders, fetchWithTimeout } from "../app/runtime";
import type { ProviderInfo, ProviderModelsResponse } from "../app/types";
import { RuntimeMark } from "../components/RuntimeMark";

type Model = { id: string; provider: string; providerName: string; model: string; local: boolean };
const FAVORITES = "echospeak.favoriteModels";

/** Chat's API agents: Echo's own harness calling each provider with your API key. Plans are for Echo Code. */
export const API_AGENTS = [
  { provider: "openai", name: "ChatGPT", mark: "codex", keyUrl: "https://platform.openai.com/api-keys", billedBy: "OpenAI", plan: "ChatGPT plan", planHome: "Codex" },
  { provider: "anthropic", name: "Claude", mark: "claude_cli", keyUrl: "https://console.anthropic.com/settings/keys", billedBy: "Anthropic", plan: "Claude plan", planHome: "Claude Code" },
] as const;
const apiAgent = (provider: string) => API_AGENTS.find(agent => agent.provider === provider);
const displayProvider = (id: string, name: string) => apiAgent(id)?.name || name;

export function ConnectedModelBrowser({ apiBase, info, provider, model, locked, onChoose, onConfigure, rail, localOnly = false }: {
  apiBase: string; info: ProviderInfo | null; provider: string; model: string; locked: boolean;
  onChoose(provider: string, model: string, models: string[]): void; onConfigure?(): void;
  rail?: React.ReactNode; localOnly?: boolean;
}) {
  const [rows, setRows] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [onlyFavorites, setOnlyFavorites] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const [view, setView] = useState<string>("all");
  const [reachable, setReachable] = useState<Record<string, boolean>>({});
  const [reload, setReload] = useState(0);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [keyError, setKeyError] = useState("");
  const [favorites, setFavorites] = useState<string[]>(() => {
    try { const value = JSON.parse(localStorage.getItem(FAVORITES) || "[]"); return Array.isArray(value) ? value.filter(v => typeof v === "string").slice(-100) : []; }
    catch { return []; }
  });
  const listId = useId();
  const providers = (info?.available_providers?.length ? info.available_providers : fallbackProviders)
    .filter(row => (!locked || row.id === provider) && (!localOnly || row.local));
  const providerIds = providers.map(row => row.id).join(",");
  useEffect(() => {
    let live = true;
    // In-process GGUF has no HTTP catalog; retain its explicitly ready model.
    setRows(!locked && provider === "llama_cpp" && info?.ready === true && info.model ? [{
      id:`${provider}:${info.model}`,provider,providerName:providers.find(row=>row.id===provider)?.name || "Local",model:info.model,local:true,
    }] : []); setLoading(true);
    // Existing authenticated catalog endpoints: no inference or provider switch.
    void Promise.allSettled(providers.map(async row => {
      const response = await fetchWithTimeout(`${apiBase}/provider/models?provider=${encodeURIComponent(row.id)}`, undefined, 20000);
      if (!response.ok) return;
      const data = await response.json() as ProviderModelsResponse;
      if (!live || data.provider !== row.id) return;
      setReachable(previous => ({ ...previous, [row.id]: data.reachable === true && Array.isArray(data.models) && data.models.length > 0 }));
      if (data.reachable !== true || !Array.isArray(data.models)) return;
      const items = [...new Set(data.models.filter(value => typeof value === "string" && value.trim()))].map(value => ({
        id: `${row.id}:${value}`, provider: row.id, providerName: displayProvider(row.id, row.name), model: value, local: row.local,
      }));
      setRows(previous => [...new Map([...previous, ...items].map(item=>[item.id,item])).values()].sort((a,b) => Number(b.local)-Number(a.local) || a.providerName.localeCompare(b.providerName) || a.model.localeCompare(b.model)));
    })).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [apiBase, providerIds, provider, info?.ready, info?.model, reload]);
  const agent = view === "all" ? undefined : apiAgent(view);
  const models = rows.filter(row => (view === "all" || row.provider === view)
    && `${row.model} ${row.providerName} ${row.local ? "local" : "API"}`.toLowerCase().includes(search.toLowerCase()) && (!onlyFavorites || favorites.includes(row.id)));
  const selected = Math.max(0, models.findIndex(row => row.id === (active || `${provider}:${model}`)));
  const choose = (row: Model) => onChoose(row.provider, row.model, rows.filter(item => item.provider === row.provider).map(item => item.model));
  const star = (id: string) => {
    const next = favorites.includes(id) ? favorites.filter(value => value !== id) : [...favorites,id].slice(-100);
    setFavorites(next); try { localStorage.setItem(FAVORITES,JSON.stringify(next)); } catch { /* Session-only favorites. */ }
  };
  const saveKey = async () => {
    if (!agent || !key.trim()) return;
    setSaving(true); setKeyError("");
    try {
      const response = await fetch(`${apiBase}/settings`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [agent.provider]: { api_key: key.trim() } }) });
      if (!response.ok) throw new Error((await response.text()) || `Couldn't save the key (${response.status})`);
      setKey("");
      window.dispatchEvent(new CustomEvent("echospeak:settings-saved", { detail: { [agent.provider]: { api_key: "***" } } }));
      setReload(value => value + 1);
    } catch (err) { setKeyError(err instanceof Error ? err.message : "Couldn't save the key"); }
    finally { setSaving(false); }
  };
  const showRail = !rail && !localOnly && !locked;
  // As soon as this provider's own check answers; local servers that are slow to answer don't hold it up.
  const needsKey = Boolean(agent && (agent.provider in reachable || !loading) && !reachable[agent.provider]);
  return <>
    <div className="es-model-browser">
      {rail || <nav className="es-provider-rail" aria-label="Model catalog">
        <button type="button" title="All connected models" aria-label="EchoSpeak models" aria-pressed={view === "all"} onClick={() => { setView("all"); setOnlyFavorites(false); setSearch(""); setActive(null); }}><RuntimeMark /></button>
        {showRail ? API_AGENTS.map(row => <button key={row.provider} type="button" title={`${row.name} (API key)`} aria-label={`${row.name} models`} aria-pressed={view === row.provider}
          onClick={() => { setView(row.provider); setSearch(""); setActive(null); setKeyError(""); }}><RuntimeMark runtime={row.mark} /></button>) : null}
      </nav>}
      <div className="es-model-results">
        <div className="es-model-search"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden><circle cx="10" cy="10" r="6" /><path d="m15 15 5 5" /></svg>
          <input autoFocus role="combobox" aria-label="Search models" aria-controls={listId} aria-expanded="true" aria-haspopup="grid" aria-autocomplete="list"
            aria-activedescendant={models.length ? `${listId}-${selected}` : undefined} placeholder={agent ? `Search ${agent.name} models…` : localOnly ? "Search models on this PC…" : "Search connected models…"} value={search}
            onChange={event => { setSearch(event.target.value); setActive(null); }} onKeyDown={event => {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); const next = (selected+(event.key === "ArrowDown" ? 1 : -1)+models.length)%Math.max(1,models.length); setActive(models[next]?.id || null); document.getElementById(`${listId}-${next}`)?.scrollIntoView({block:"nearest"}); }
              if (event.key === "Enter" && models[selected]) { event.preventDefault(); choose(models[selected]); }
            }} />
          <button type="button" className="es-catalog-favorites" aria-label="Favorite models" aria-pressed={onlyFavorites} onClick={() => { setOnlyFavorites(value => !value); setActive(null); }}>★</button>
        </div>
        {needsKey && agent ? <form className="es-api-connect" aria-label={`Connect ${agent.name} with an API key`} onSubmit={event => { event.preventDefault(); void saveKey(); }}>
          <div className="es-api-connect-head"><RuntimeMark runtime={agent.mark} /><strong>Use {agent.name} in Chat</strong></div>
          <p>Chat calls {agent.name} with your {agent.billedBy} API key through Echo's own tools and memory. API use is billed to your {agent.billedBy} account, not your {agent.plan}. Your {agent.plan} works in Echo Code through {agent.planHome}.</p>
          <label>{agent.billedBy} API key<input type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)} placeholder="Paste your API key" /></label>
          <div className="es-api-connect-actions"><button type="submit" className="es-btn es-btn-sm es-btn-primary" disabled={saving || !key.trim()}>{saving ? "Connecting…" : `Connect ${agent.name}`}</button>
            <a href={agent.keyUrl} target="_blank" rel="noopener noreferrer">Get a key ↗</a></div>
          {keyError ? <small role="alert">{keyError}</small> : <small>Stored encrypted on this PC. Change or remove it in Settings › Models.</small>}
        </form> : null}
        <div className="es-model-list" role="grid" id={listId} aria-label={onlyFavorites ? "Favorite models" : "Connected models"} aria-busy={loading}>
          {models.map((row,index) => <div key={row.id} id={`${listId}-${index}`} role="row" aria-selected={row.provider === provider && row.model === model} className={`es-model-row${index === selected ? " is-highlighted" : ""}`} onPointerMove={() => setActive(row.id)}>
            <div role="gridcell"><button type="button" className="es-model-choice" onClick={() => choose(row)}><strong>{row.model}</strong><small>{row.providerName} · {row.local ? "On this PC" : "API"}{row.provider === provider && row.model === model ? " · Current" : ""}</small></button></div>
            <div role="gridcell"><button type="button" className="es-model-star" aria-label={`Favorite ${row.model}`} aria-pressed={favorites.includes(row.id)} onClick={() => star(row.id)}>{favorites.includes(row.id) ? "★" : "☆"}</button></div>
          </div>)}
          {!models.length && !needsKey ? <p role="status">{loading ? "Checking connections…" : search ? "No matching models." : onlyFavorites ? "No favorite connected models."
            : localOnly ? "No models on this PC yet. In Echo Code, use Codex or Claude Code with your plan, or set up a local model." : "No connected models. Configure providers to connect one."}</p> : null}
        </div>
      </div>
    </div>
    <footer><span>{loading ? "Checking connections…" : localOnly ? "Models on this PC" : agent ? `${agent.name} · API key` : "Connected models"}</span>{onConfigure ? <button type="button" onClick={onConfigure}>Configure providers ↗</button> : null}</footer>
  </>;
}
