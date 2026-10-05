import React, { useEffect, useState } from "react";
import { PageShell } from "../lean/Pages";
import { useSettings } from "../settings/useSettings";
import { CreationCard, CreationPreview } from "./CreationCard";
import { creationRequest, type CreationAsset, type CreationJob } from "./api";
import { GenerationSettings } from "./GenerationSettings";

export function CreationsPage({ apiBase, onChat }: { apiBase: string; onChat(id: string): void }) {
  const [assets, setAssets] = useState<CreationAsset[]>([]);
  const [jobs, setJobs] = useState<CreationJob[]>([]);
  const [archived, setArchived] = useState(false);
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const settings = useSettings(apiBase);
  useEffect(() => {
    let disposed = false;
    const refresh = async () => {
      try { const data = await creationRequest(apiBase, `/creations?archived=${archived}&kind=${kind}&query=${encodeURIComponent(query)}`); if (!disposed) { setAssets(data.items); setJobs(data.jobs); setError(""); setLoaded(true); } }
      catch (e) { if (!disposed) { setError(e instanceof Error ? e.message : String(e)); setLoaded(true); } }
    };
    void refresh(); const timer = setInterval(refresh, 5000);
    return () => { disposed = true; clearInterval(timer); };
  }, [apiBase, archived, kind, query]);
  const update = async (asset: CreationAsset, patch: unknown) => {
    try { await creationRequest(apiBase, `/creations/assets/${encodeURIComponent(asset.id)}`, "PATCH", patch); const data = await creationRequest(apiBase, `/creations?archived=${archived}&kind=${kind}&query=${encodeURIComponent(query)}`); setAssets(data.items); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return <PageShell title="Creations" lead="Images and videos you create with Echo, together in one place." action={<button type="button" className="es-btn es-btn-primary" onClick={() => setSettingsOpen(!settingsOpen)}>{settingsOpen ? "Back to library" : "Creation settings"}</button>}>
    {settingsOpen ? settings.settings ? <><GenerationSettings s={settings.settings} save={settings.save} apiBase={apiBase} /><p role="status">{settings.saveError || settings.saveState}</p></> : <p>{settings.error || "Loading settings…"}</p> : <>
      <div className="creation-filters"><input aria-label="Search creations" placeholder="Search names and prompts" value={query} onChange={e => setQuery(e.target.value)} /><select aria-label="Media type" value={kind} onChange={e => setKind(e.target.value)}><option value="">All media</option><option value="image">Images</option><option value="video">Videos</option></select><label><input type="checkbox" checked={archived} onChange={e => setArchived(e.target.checked)} /> Archived</label></div>
      {error && <p role="alert">{error}</p>}
      {!archived && jobs.slice(0, 12).map(job => <CreationCard key={job.id} id={job.id} apiBase={apiBase} />)}
      {!loaded && <div className="es-sec-empty" role="status">Loading creations…</div>}
      {loaded && !assets.length && (archived || !jobs.length) && <button type="button" className="es-page-empty" onClick={() => setSettingsOpen(true)}><strong>{query || kind || archived ? "No matching creations" : "No creations yet"}</strong><span>{query || kind || archived ? "Try another search or media filter." : "Choose a creation provider, then ask Echo for an image or video in a chat."}</span></button>}
      <div className="es-page-grid creation-library-grid">{assets.map(asset => <article className="es-page-card creation-library-card" key={asset.id}>
        <CreationPreview asset={asset} apiBase={apiBase} />
        <input aria-label="Creation name" defaultValue={asset.name} key={`${asset.id}-${asset.name}`} maxLength={160} onBlur={e => { if (e.target.value.trim() && e.target.value !== asset.name) void update(asset, { name: e.target.value.trim() }); }} />
        <p>{asset.prompt}</p><small>{asset.provider} · {asset.model}</small>
        <div className="creation-actions"><button className="es-btn es-btn-sm" onClick={() => onChat(asset.session_id)}>Open chat</button><button className="es-btn es-btn-sm" onClick={() => void update(asset, { archived: !asset.archived })}>{asset.archived ? "Restore" : "Archive"}</button></div>
      </article>)}</div>
    </>}
  </PageShell>;
}
