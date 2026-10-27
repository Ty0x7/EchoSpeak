import React, { useState } from "react";
import { RuntimeMark } from "../components/RuntimeMark";
import { ConnectedModelBrowser } from "../dashboard/ConnectedModelBrowser";
import type { ProviderInfo } from "../app/types";
import { agentModelName, type AgentModel, type RuntimeCatalog } from "./model";

export type NativeModel = { provider: string; model: string };
export function CodeModelPicker(p: {
  apiBase: string; catalog: RuntimeCatalog; runtimeId: string; onRuntime(id: string): void;
  nativeInfo: ProviderInfo | null; nativeChoice: NativeModel | null; nativeLocked: boolean;
  onNative(choice: NativeModel): void; onConfigureNative(): void;
  models: AgentModel[]; model: string; reportedModel: string; connected: boolean;
  onModel(model: string): void; onConfigureRuntime(): void;
}) {
  const [search, setSearch] = useState("");
  const [custom, setCustom] = useState("");
  const runtime = p.catalog.runtimes.find(row => row.id === p.runtimeId);
  const rail = <nav className="es-provider-rail" aria-label="Coding agents">{p.catalog.runtimes.map(row =>
    <button key={row.id} type="button" title={row.name} aria-label={`${row.name} models`} aria-pressed={row.id === p.runtimeId}
      onClick={() => { p.onRuntime(row.id); setSearch(""); setCustom(""); }}><RuntimeMark runtime={row.id} /></button>)}</nav>;
  if (p.runtimeId === "echo") return <ConnectedModelBrowser apiBase={p.apiBase} info={p.nativeInfo}
    provider={p.nativeChoice?.provider || p.nativeInfo?.provider || ""} model={p.nativeChoice?.model || p.nativeInfo?.model || ""}
    locked={p.nativeLocked} rail={rail} onChoose={(provider, model) => p.onNative({ provider, model })} onConfigure={p.onConfigureNative} />;
  const current = agentModelName(p.models, p.model, p.reportedModel);
  const rows = p.models.filter(row => `${row.displayName || ""} ${row.model}`.toLowerCase().includes(search.toLowerCase()));
  return <><div className="es-model-browser">{rail}<div className="es-model-results">
    <div className="es-model-search"><input autoFocus aria-label="Search agent models" placeholder={`Search ${runtime?.name || "agent"} models…`}
      value={search} onChange={event => setSearch(event.target.value)} /></div>
    <div className="ec-model-current"><RuntimeMark runtime={p.runtimeId} /><span><strong>{current || "Model not reported"}</strong>
      <small>{runtime?.name} · {p.connected ? p.model ? "Selected for next task" : "Connected" : current ? "Saved selection / last reported" : "Not connected"}</small></span></div>
    <div className="es-model-list" role="grid" aria-label="Agent models">{rows.map(row => <div className="es-model-row" role="row" key={row.model}
      aria-selected={row.model === (p.model || p.reportedModel || p.models.find(item => item.isDefault)?.model)}><div role="gridcell">
      <button type="button" className="es-model-choice" disabled={p.runtimeId === "acp"} onClick={() => p.onModel(row.model)}>
        <strong>{row.displayName || row.model}</strong><small>{runtime?.name}{row.model === (p.model || p.reportedModel) ? " · Current" : row.isDefault ? " · Default" : ""}</small></button>
    </div></div>)}
      {!rows.length ? <p role="status">{search ? "No matching models." : p.connected ? "This harness has not reported a model catalog." : "Connect this agent to see the models it reports. Your draft stays visible."}</p> : null}
      {p.runtimeId !== "acp" ? <button className="es-model-choice" onClick={() => p.onModel("")}><strong>Use harness default</strong><small>The actual model appears when reported.</small></button> : null}
      {p.runtimeId === "claude_code" || p.runtimeId === "claude_cli" ? <form className="ec-custom-model" onSubmit={event => { event.preventDefault(); if (custom.trim()) p.onModel(custom.trim()); }}>
        <label>Model ID<input aria-label="Claude model ID" placeholder="Enter an available Claude model ID" maxLength={200} value={custom} onChange={event => setCustom(event.target.value)} /></label>
        <button type="submit" disabled={!custom.trim()}>Select model</button><small>{p.runtimeId === "claude_cli" ? "Uses your Claude Code sign-in and plan." : "Uses this harness's API account."} Availability is checked by the harness when you send.</small>
      </form> : null}
      {p.runtimeId === "acp" ? <p>The ACP agent manages its model. Reported model information is shown here.</p> : null}
    </div></div></div><footer><span>{runtime?.name} · separate harness</span><button onClick={p.onConfigureRuntime}>Connection &amp; session ↗</button></footer></>;
}
