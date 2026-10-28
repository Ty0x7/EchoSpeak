import React, { useState } from "react";
import { RuntimeMark } from "../components/RuntimeMark";
import { ConnectedModelBrowser } from "../dashboard/ConnectedModelBrowser";
import type { ProviderInfo } from "../app/types";
import { agentModelName, planLabel, type AgentAccount, type AgentModel, type RuntimeCatalog } from "./model";

export type NativeModel = { provider: string; model: string };
export function CodeModelPicker(p: {
  apiBase: string; catalog: RuntimeCatalog; runtimeId: string; onRuntime(id: string): void;
  nativeInfo: ProviderInfo | null; nativeChoice: NativeModel | null; nativeLocked: boolean;
  onNative(choice: NativeModel): void; onConfigureNative(): void;
  models: AgentModel[]; model: string; reportedModel: string; connected: boolean; signedIn: boolean; account: AgentAccount | null;
  onModel(model: string): void; onConfigureRuntime(): void;
}) {
  const [search, setSearch] = useState("");
  const [custom, setCustom] = useState("");
  const runtime = p.catalog.runtimes.find(row => row.id === p.runtimeId);
  const rail = <nav className="es-provider-rail" aria-label="Coding agents">{p.catalog.runtimes.map(row =>
    <button key={row.id} type="button" title={row.name} aria-label={`${row.name} models`} aria-pressed={row.id === p.runtimeId}
      onClick={() => { p.onRuntime(row.id); setSearch(""); setCustom(""); }}><RuntimeMark runtime={row.id} /></button>)}</nav>;
  // Plans for code, APIs for chat: Echo native in Echo Code lists models on this PC only.
  if (p.runtimeId === "echo") return <ConnectedModelBrowser apiBase={p.apiBase} info={p.nativeInfo} localOnly
    provider={p.nativeChoice?.provider || p.nativeInfo?.provider || ""} model={p.nativeChoice?.model || p.nativeInfo?.model || ""}
    locked={p.nativeLocked} rail={rail} onChoose={(provider, model) => p.onNative({ provider, model })} onConfigure={p.onConfigureNative} />;
  const current = agentModelName(p.models, p.model, p.reportedModel);
  const selected = p.model || p.reportedModel || p.models.find(item => item.isDefault)?.model;
  const rows = p.models.filter(row => `${row.displayName || ""} ${row.model} ${row.description || ""}`.toLowerCase().includes(search.toLowerCase()));
  const plan = planLabel(p.account);
  const status = p.connected ? (p.model ? "Selected for your next task" : "Connected") : p.signedIn ? `Signed in${plan ? ` · ${plan}` : ""}` : p.runtimeId === "acp" ? "Not connected" : "Not signed in";
  return <><div className="es-model-browser">{rail}<div className="es-model-results">
    <div className="es-model-search"><input autoFocus aria-label="Search agent models" placeholder={`Search ${runtime?.name || "agent"} models…`}
      value={search} onChange={event => setSearch(event.target.value)} /></div>
    <div className="ec-model-current"><RuntimeMark runtime={p.runtimeId} /><span><strong>{current || "Harness default"}</strong>
      <small>{runtime?.name} · {status}</small></span></div>
    <div className="es-model-list" role="grid" aria-label="Agent models">{rows.map(row => <div className="es-model-row" role="row" key={row.model}
      aria-selected={row.model === selected}><div role="gridcell">
      <button type="button" className="es-model-choice" disabled={p.runtimeId === "acp"} onClick={() => p.onModel(row.model)}>
        <strong>{row.displayName || row.model}</strong><small>{row.description || runtime?.name}{row.model === selected ? " · Current" : row.isDefault ? " · Default" : ""}</small></button>
    </div></div>)}
      {!rows.length ? <p role="status">{search ? "No matching models." : p.runtimeId === "acp" ? "Connect this agent to see the models it reports."
        : p.signedIn ? "Checking which models your plan offers…" : `Sign in to ${runtime?.name || "this agent"} to see the models your plan offers. Your draft stays here.`}</p> : null}
      {p.runtimeId !== "acp" ? <button className="es-model-choice" onClick={() => p.onModel("")}><strong>Use {runtime?.name || "the agent"}'s default</strong><small>The model it actually uses appears once it reports it.</small></button> : null}
      {p.runtimeId === "claude_cli" ? <form className="ec-custom-model" onSubmit={event => { event.preventDefault(); if (custom.trim()) p.onModel(custom.trim()); }}>
        <label>Model ID<input aria-label="Claude model ID" placeholder="A full model name, e.g. claude-sonnet-5-5" maxLength={200} value={custom} onChange={event => setCustom(event.target.value)} /></label>
        <button type="submit" disabled={!custom.trim()}>Select model</button><small>Uses your Claude Code sign-in and plan. Claude Code checks availability when you send.</small>
      </form> : null}
      {p.runtimeId === "acp" ? <p>The ACP agent manages its model. Reported model information is shown here.</p> : null}
    </div></div></div><footer><span>{runtime?.name} · its own harness{plan ? ` · ${plan}` : ""}</span><button onClick={p.onConfigureRuntime}>Connection &amp; session ↗</button></footer></>;
}
