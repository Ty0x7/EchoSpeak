import React, { useEffect, useState } from "react";
import { request } from "../projects/model";

type Client = { id: string; name: string; control: boolean; expires_at: number };
export function CodeControl({ apiBase, projects }: { apiBase: string; projects: { id: string; name: string }[] }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [agents, setAgents] = useState<{id:string;name:string}[]>([]);
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [write, setWrite] = useState(false);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [diagnostics, setDiagnostics] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [bridge, setBridge] = useState<{command:string;args:string[];cwd?:string}|null>(null);
  useEffect(()=>{let live=true;void request<{command:string;args:string[];cwd?:string}>(`${apiBase}/echo-code/control/bridge`).then(d=>{if(live)setBridge(d)}).catch(()=>{});return()=>{live=false}},[apiBase]);
  const refresh = () => request<{clients:Client[]}>(`${apiBase}/echo-code/control/clients`).then(d => setClients(d.clients));
  useEffect(() => { let live=true; void refresh().catch(e=>{if(live)setError(e.message)}); void request<{items:{id:string;name:string}[]}>(`${apiBase}/lean/agents`).then(d=>{if(live)setAgents(d.items || [])}).catch(()=>{}); return()=>{live=false}; }, [apiBase]);
  const download = async () => {
    try {
      const data = await request(`${apiBase}/echo-code/diagnostics`);
      setDiagnostics(data);
      const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
      const link=document.createElement("a"); link.href=url; link.download="echospeak-triage.json"; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(e) {setError(e instanceof Error?e.message:"Diagnostics unavailable")}
  };
  return <section className="ec-control" aria-label="MCP access and diagnostics">
    <strong>MCP access</strong><p>Choose exactly which projects or agents a client may see. Read only by default. Access expires after seven days.</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError("");try{const d=await request<{token:string}>(`${apiBase}/echo-code/control/clients`,{name,projects:selected,agents:agentIds,control:write});setToken(d.token);await refresh()}catch(e){setError(e instanceof Error?e.message:"Could not create access")}finally{setBusy(false)}}}>
      <input aria-label="MCP client name" value={name} onChange={e=>setName(e.target.value)} placeholder="Client name" required maxLength={80}/>
      <fieldset><legend>Projects</legend>{projects.map(p=><label key={p.id}><input type="checkbox" checked={selected.includes(p.id)} onChange={()=>setSelected(v=>v.includes(p.id)?v.filter(id=>id!==p.id):[...v,p.id])}/>{p.name}</label>)}</fieldset>
      {agents.length ? <fieldset><legend>Persistent agents</legend>{agents.map(a=><label key={a.id}><input type="checkbox" checked={agentIds.includes(a.id)} onChange={()=>setAgentIds(v=>v.includes(a.id)?v.filter(id=>id!==a.id):[...v,a.id])}/>{a.name}</label>)}</fieldset> : null}
      <label><input type="checkbox" checked={write} onChange={e=>setWrite(e.target.checked)}/>Allow create, send, stop and inbox changes</label>
      <button type="submit" disabled={busy || !name.trim() || (!selected.length&&!agentIds.length)}>Create client access</button>
    </form>
    {token ? <div><p>Copy this separate client token now. It will not be shown again. Keep it in your MCP client's environment.</p><textarea aria-label="New MCP client token" readOnly value={token}/><button type="button" onClick={()=>setToken("")}>Hide token</button>
      <p>Use this configuration with an external MCP client, or in Echo's existing MCP connections to let approved agents use these tools. The URL targets this running backend.</p>
      {bridge?<pre>{JSON.stringify({mcpServers:{EchoCode:{...bridge,env:{ECHOSPEAK_CONTROL_URL:new URL(apiBase||"/",window.location.href).origin,ECHOSPEAK_CONTROL_TOKEN:token}}}},null,2)}</pre>:null}</div> : null}
    {clients.map(c=><div className="ec-control-client" key={c.id}><span>{c.name} · {c.control?"Control":"Read only"}</span><small>Expires {new Date(c.expires_at*1000).toLocaleDateString()}</small><button type="button" onClick={async()=>{try{const r=await fetch(`${apiBase}/echo-code/control/clients/${encodeURIComponent(c.id)}`,{method:"DELETE"});if(!r.ok)throw new Error("Could not revoke access");await refresh();setToken("")}catch(e){setError(e instanceof Error?e.message:"Revoke failed")}}}>Revoke</button></div>)}
    <hr/><strong>Local diagnostics</strong><p>Export a small report of versions, runtime states and CPU/memory observations. Conversation text, credentials, prompts and raw logs are excluded. Nothing is uploaded.</p><button type="button" onClick={()=>void download()}>Save triage report</button>
    {diagnostics ? <details><summary>Report preview</summary><pre>{JSON.stringify(diagnostics,null,2)}</pre></details> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
