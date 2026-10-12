import React, { useCallback, useEffect, useState } from "react";
import type { LeanPersona } from "../lean/types";
import { ChoiceCards, Group, ListEditor, Row, TextField, Toggle } from "./controls";
import type { SettingsMap } from "./useSettings";

/**
 * Settings › Models › Smart model choice (backend: agent/learning/routing.py, GET /lean/routing).
 * Off by default. Learns which of your models does each kind of task best from what actually
 * happened on this PC, and suggests or (for agents you opt in) picks accordingly.
 */

type Record_ = { wins: number; losses: number; decided: number; errors: Record<string, number>; avg_seconds: number | null; avg_tokens: number | null };
type PoolRow = { provider: string; model: string; cloud: boolean; available: boolean; why: string; auto_allowed: boolean; auto_why: string };
type ModelRow = { provider: string; model: string; listed: boolean; cloud: boolean; overall: Record_; kinds: Record<string, Record_> };
type Routing = { mode: string; pool: PoolRow[]; models: ModelRow[]; cloud_tokens_today: number; min_evidence: number };

const MODES = [
  { value: "off", title: "Off", body: "Every agent uses the model you set. Nothing is suggested or changed." },
  { value: "suggest", title: "Suggest",
    body: "When another model you listed has done clearly better at that kind of task here, the agent says so under its reply. Nothing switches." },
  { value: "auto", title: "Choose for me",
    body: "For the agents you pick below, Echo switches to the listed model with the best record for the task, and says why under the reply." },
];

const kindLabel = (kind: string) => kind.replace(/_/g, " ");
const record = (r: Record_) => (r.decided ? `${r.wins}/${r.decided} done with proof` : "no checked tasks yet");

export function SmartModelChoice({ s, save, apiBase, agents }: { s: SettingsMap; save(patch: SettingsMap): Promise<void>; apiBase: string; agents: LeanPersona[] }) {
  const [data, setData] = useState<Routing | null>(null);
  const mode = String(s.routing_mode || "off");
  const pool: string[] = Array.isArray(s.routing_pool) ? s.routing_pool : [];
  const autoAgents: string[] = Array.isArray(s.routing_auto_agents) ? s.routing_auto_agents : [];

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/lean/routing`, { cache: "no-store" });
      if (res.ok) setData((await res.json()) as Routing);
    } catch { /* the settings still work without the records */ }
  }, [apiBase]);
  useEffect(() => { void load(); }, [load, mode, pool.join("|")]);

  const toggleAgent = (id: string, on: boolean) =>
    void save({ routing_auto_agents: on ? [...new Set([...autoAgents, id])] : autoAgents.filter((a) => a !== id) });

  return (
    <>
      <Group title="Smart model choice" description="Learns which of your models does each kind of task best, from what actually happened here. Your own picks always win unless you let Echo choose for an agent.">
        <ChoiceCards value={mode} options={MODES} onChange={(v) => void save({ routing_mode: v })} />
      </Group>

      {mode !== "off" ? (
        <>
          <Group title="Models Echo may use" description="Only these are ever suggested or picked, written as provider:model (for example ollama:qwen3:8b or gemini:gemini-flash). Private mode still applies.">
            <ListEditor items={pool} onChange={(items) => void save({ routing_pool: items })} placeholder="lmstudio:gemma-4-12b" mono />
            {(data?.pool || []).map((p) => (
              <Row key={`${p.provider}:${p.model}`} label={<span className="is-mono">{p.model} ({p.provider})</span>}
                help={p.available ? (p.cloud ? (p.auto_allowed ? "Available. Paid cloud model, allowed for automatic picks." : `Available for suggestions. Not picked automatically: ${p.auto_why}.`) : "Available on your machines.") : `Not available now: ${p.why}.`} />
            ))}
          </Group>

          {mode === "auto" ? (
            <Group title="Agents Echo may switch" description="Everyone else keeps the model you set for them.">
              {agents.map((a) => (
                <Row key={a.id} label={a.name} help={a.model?.model_id ? `Set to ${a.model.model_id}` : "Uses the chat's model"}>
                  <Toggle checked={autoAgents.includes(a.id)} onChange={(v) => toggleAgent(a.id, v)} label={`Let Echo choose ${a.name}'s model`} />
                </Row>
              ))}
              <Row label="Allow paid cloud models" help="Off: automatic picks stay on local models. Suggestions can still mention cloud models you listed.">
                <Toggle checked={Boolean(s.routing_allow_cloud)} onChange={(v) => void save({ routing_allow_cloud: v })} label="Allow paid cloud models" />
              </Row>
              {s.routing_allow_cloud ? (
                <Row label="Daily cloud token cap" help={`Tokens automatic picks may use on cloud models per day; 0 = no cap. Used today: ${(data?.cloud_tokens_today || 0).toLocaleString()}.`}>
                  <TextField type="number" value={Number(s.routing_daily_cloud_tokens || 0)} onCommit={(v) => void save({ routing_daily_cloud_tokens: Math.max(0, Number(v) || 0) })} />
                </Row>
              ) : null}
            </Group>
          ) : null}

          <Group title="What Echo has learned" description={`Tasks finished with proof (checks, tests, or your "Worked"), per kind of task. A model needs ${data?.min_evidence || 5} decided tasks of a kind before it's compared. Outages and missing keys never count against a model.`}>
            {(data?.models || []).length ? (data!.models.map((m) => (
              <Row key={`${m.provider}:${m.model}`}
                label={<span><span className="is-mono">{m.model}</span> ({m.provider}){m.listed ? "" : " · not listed"}</span>}
                help={<>
                  <span>Overall {record(m.overall)}.</span>
                  {Object.entries(m.kinds).filter(([, r]) => r.decided).slice(0, 5).map(([kind, r]) => <span key={kind}> {kindLabel(kind)}: {r.wins}/{r.decided}.</span>)}
                  {Object.keys(m.overall.errors || {}).length ? <span> Not counted: {Object.entries(m.overall.errors).map(([k, n]) => `${n} ${k}`).join(", ")}.</span> : null}
                  {m.overall.avg_seconds ? <span> About {m.overall.avg_seconds}s per task.</span> : null}
                </>} />
            ))) : <Row label={<span className="st-muted">Nothing yet. Records grow as you use Echo with learning on.</span>} />}
          </Group>
        </>
      ) : null}
    </>
  );
}
