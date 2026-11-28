import React, { useEffect, useState } from "react";
import { Group, ListEditor, Row, Select, Status, TextField, Toggle } from "./controls";
import type { SettingsMap } from "./useSettings";

type Save = (patch: Record<string, unknown>) => void | Promise<void>;
type Scope = { enabled: boolean; capture_prompts: boolean; agents: Record<string, boolean>; roots: string[] };
type Hooks = { claude_code: boolean; codex_hooks: boolean; hook_installed: boolean };
type Session = { label: string; project: string; state: string; agent: string; interacting?: boolean };

const STATE_LABEL: Record<string, string> = {
  working: "working", your_turn: "waiting for you", needs_permission: "needs permission",
  quiet: "quiet", idle: "idle", ended: "ended",
};

/** Settings › Echo Companion: what it watches, what it says, what it keeps, and how it looks. */
export function CompanionSettings({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const [scope, setScope] = useState<Scope | null>(null);
  const [hooks, setHooks] = useState<Hooks | null>(null);
  const [autostart, setAutostart] = useState<{ enabled: boolean; available: boolean } | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [voices, setVoices] = useState<string[]>([]);
  const [soul, setSoul] = useState<{ text: string; default: string } | null>(null);
  const [soulDraft, setSoulDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [visibility, setVisibility] = useState("unknown");
  const get = (path: string) => fetch(`${apiBase}${path}`).then(r => (r.ok ? r.json() : null)).catch(() => null);
  const send = async (method: string, path: string, body: unknown) => {
    const r = await fetch(`${apiBase}${path}`, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(String(data.detail || "That didn't work."));
    return data;
  };
  const refresh = () => {
    void get("/companion/presentation").then(d => d && setVisibility(d.visibility));
    void get("/companion/scope").then(d => d && setScope(d));
    void get("/companion/hooks/status").then(d => d && setHooks(d));
    void get("/companion/autostart").then(d => d && setAutostart(d));
    void get("/companion/activity").then(d => d && setSessions((d.sessions || []).slice(0, 8)));
  };
  useEffect(() => {
    refresh();
    void get("/media-runtime/voice/kokoro/voices").then(d => d && setVoices(d.items || []));
    void get("/companion/soul").then(d => { if (d) { setSoul(d); setSoulDraft(d.text); } });
    const timer = window.setInterval(() => {
      void get("/companion/presentation").then(d => d && setVisibility(d.visibility));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [apiBase]);

  const enabled = Boolean(s.companion_enabled);
  const scopeMode = String(s.companion_memory_scope || "project_only");
  const updateScope = async (patch: Partial<Scope>) => {
    try { setScope(await send("PUT", "/companion/scope", patch)); setNotice(""); }
    catch (e) { setNotice(e instanceof Error ? e.message : String(e)); }
  };
  const hookAction = async (agent: "claude-code" | "codex", action: "install" | "uninstall") => {
    try { const r = await send("POST", "/companion/hooks", { agent, action }); setHooks(r.status); setNotice(""); }
    catch (e) { setNotice(e instanceof Error ? e.message : String(e)); }
  };

  return <>
    <Group title="Echo Companion" description="Echo on your desktop: it notices when Claude Code, Codex, Echo or your agents finish something, says so, and keeps what they learned. It never changes what they do.">
      <Row label="Turn on Echo Companion" help="Off: nothing is observed, said or kept.">
        <Toggle checked={enabled} onChange={v => save({ companion_enabled: v })} label="Turn on Echo Companion" />
      </Row>
      <Row label="Start at login" help={autostart?.available === false ? "This build doesn't include the desktop companion." : "Adds Echo Companion to your desktop's autostart."}>
        <Toggle checked={Boolean(autostart?.enabled)} disabled={!autostart?.available}
          onChange={v => void send("PUT", "/companion/autostart", { enabled: v }).then(d => setAutostart({ enabled: d.enabled, available: true })).catch(e => setNotice(String(e.message || e)))}
          label="Start at login" />
      </Row>
      <Row label="Recover recent activity" help="Read recent Claude Code and Codex session records in the EchoSpeak projects you’ve selected for observation.">
        <Toggle checked={s.companion_recover_history !== false}
          onChange={v => save({ companion_recover_history: v })} label="Recover recent activity" />
      </Row>
      {notice ? <Row label={<Status tone="err">{notice}</Status>} /> : null}
    </Group>

    <Group title="What it says" description="Short, grounded lines about real events. Results an agent only reported are said as reported.">
      <Row label="Speak commentary" help="Off keeps captions only.">
        <Toggle checked={s.companion_speak !== false} onChange={v => save({ companion_speak: v })} label="Speak commentary" />
      </Row>
      <Row label="How often" help="Quiet: finished reports only. Milestones: also when an agent you aren't looking at needs you. Chatty: also long background turns.">
        <Select value={String(s.companion_frequency || "milestones")} onChange={v => save({ companion_frequency: v })}
          options={[{ value: "quiet", label: "Quiet" }, { value: "milestones", label: "Milestones" }, { value: "chatty", label: "Chatty" }]} />
      </Row>
        <Row label="Long-task updates" help="After this much time, a newly observed completed operation can prompt an update.">
        <Select value={String(s.companion_progress_minutes ?? 30)} onChange={v => save({ companion_progress_minutes: Number(v) })}
          options={[{ value: "0", label: "Off" }, ...[15, 30, 60].map(v => ({ value: String(v), label: `${v} minutes` }))]} />
      </Row>
      <Row label="Overall check-ins" help="Review active work and recent results. Echo stays quiet when there is nothing meaningful to add.">
        <Select value={String(s.companion_checkin_minutes ?? 0)} onChange={v => save({ companion_checkin_minutes: Number(v) })}
          options={[{ value: "0", label: "Off" }, { value: "30", label: "Every 30 minutes" }, { value: "60", label: "Every 60 minutes" }]} />
      </Row>
      <Row label="Humour">
        <Select value={String(s.companion_humor ?? 1)} onChange={v => save({ companion_humor: Number(v) })}
          options={[{ value: "0", label: "None" }, { value: "1", label: "Light" }, { value: "2", label: "Playful" }]} />
      </Row>
      <Row label="Wording" help="Phrases: your SOUL's lines, no model. Local model: your local model rewords each line; any rewrite that adds a fact is thrown away.">
        <Select value={String(s.companion_soul_style || "templates")} onChange={v => save({ companion_soul_style: v })}
          options={[{ value: "templates", label: "Phrases (fastest)" }, { value: "local_model", label: "Local model" }]} />
      </Row>
      <Row label="Quiet hours" help="e.g. 22:00-08:00. Nothing is spoken then; captions and memory continue.">
        <TextField value={String(s.companion_quiet_hours || "")} onCommit={v => save({ companion_quiet_hours: v.trim() })} placeholder="22:00-08:00" />
      </Row>
      <Row label="Voice" help="Uses Echo's voice unless you pick another.">
        <Select value={String(s.companion_voice || "")} onChange={v => save({ companion_voice: v })}
          options={[{ value: "", label: "Same as Echo" }, ...voices.map(v => ({ value: v, label: v }))]} />
      </Row>
    </Group>

    <Group title="On screen">
      <Row label="Auto-hide when idle" help="Slide Echo away when idle or waiting.">
        <Toggle checked={s.companion_auto_hide !== false} onChange={v => save({ companion_auto_hide: v })}
          label="Auto-hide when idle" />
      </Row>
      <Row label="Companion window" help={enabled ? visibility : "Disabled"}>
        {(["show", "hide"] as const).map(action => (
          <button key={action} type="button" className="es-btn es-btn-sm" disabled={!enabled}
            onClick={() => void send("POST", `/companion/presentation/${action}`, {})
              .then(() => setNotice("")).catch(e => setNotice(String(e.message || e)))}>{action === "show" ? "Show Echo" : "Hide Echo"}</button>
        ))}
      </Row>
      <Row label="Position">
        <Select value={String(s.companion_position || "top-right")} onChange={v => save({ companion_position: v })}
          options={["top-right", "top-left", "bottom-right", "bottom-left"].map(v => ({ value: v, label: v.replace("-", " ") }))} />
      </Row>
      <Row label="Captions" help="A small speech bubble that disappears shortly after Echo stops talking.">
        <Toggle checked={s.companion_captions !== false} onChange={v => save({ companion_captions: v })} label="Captions" />
      </Row>
      <Row label="Animation">
        <Select value={String(s.companion_animation || "full")} onChange={v => save({ companion_animation: v })}
          options={[{ value: "full", label: "Full" }, { value: "reduced", label: "Reduced motion" }]} />
      </Row>
    </Group>

    <Group title="What it keeps" description="Reports are kept with who wrote them and whether EchoSpeak checked them. Nothing changes Echo's SOUL, permissions or file access.">
      <Row label="Keep what agents learn" help="Saves finished reports into Echo's memory.">
        <Toggle checked={s.companion_knowledge !== false} onChange={v => save({ companion_knowledge: v })} label="Keep what agents learn" />
      </Row>
      <Row label="Where" help={scopeMode === "project_or_account"
        ? "Reports from folders outside your projects become visible to every conversation."
        : "Only inside the matching EchoSpeak project. Reports from other folders are not kept."}>
        <Select value={scopeMode} onChange={v => save({ companion_memory_scope: v })}
          options={[{ value: "project_only", label: "Project only (recommended)" },
                    { value: "project_or_account", label: "Project, else everywhere (broader)" },
                    { value: "off", label: "Nowhere" }]} />
      </Row>
    </Group>

    <Group title="Agents it watches" description="Claude Code and Codex report through their own official hooks; only the folders below are observed, and tool inputs and outputs are never kept.">
      <Row label="Echo and your agents">
        <Toggle checked={s.companion_observe_echo !== false} onChange={v => save({ companion_observe_echo: v })} label="Echo and your agents" />
      </Row>
      {(["claude-code", "codex"] as const).map(agent => {
        const installed = agent === "claude-code" ? hooks?.claude_code : hooks?.codex_hooks;
        const label = agent === "claude-code" ? "Claude Code" : "Codex";
        return <Row key={agent} label={label} help={installed ? "Hook installed." : "Hook not installed."}>
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Toggle checked={Boolean(scope?.agents[agent])} disabled={!installed}
              onChange={v => void updateScope({ agents: { ...(scope?.agents || {}), [agent]: v } })} label={`Observe ${label}`} />
            <button className="es-btn es-btn-sm" onClick={() => void hookAction(agent, installed ? "uninstall" : "install")}>
              {installed ? "Remove hook" : "Install hook"}
            </button>
          </span>
        </Row>;
      })}
      <Row label="Record your prompts" help="Up to 600 characters, secrets masked, so Echo knows what each agent is working on.">
        <Toggle checked={Boolean(scope?.capture_prompts)} onChange={v => void updateScope({ capture_prompts: v })} label="Record your prompts" />
      </Row>
      <Row label="Observed folders" help="Project folders only; your whole home folder is refused." stack>
        <ListEditor items={scope?.roots || []} onChange={roots => void updateScope({ roots })} placeholder="/home/you/Work" mono />
      </Row>
      <Row label="Seen right now" stack>
        <div className="st-muted" style={{ display: "grid", gap: 2 }}>
          {sessions.length ? sessions.map((row, i) => <span key={i}>{row.label}{row.project ? ` · ${row.project}` : ""} · {STATE_LABEL[row.state] || row.state}{row.interacting ? " · you're here" : ""}</span>)
            : <span>Nothing active.</span>}
        </div>
      </Row>
    </Group>

    <Group title="Companion SOUL" description="Echo Companion's own personality and phrases. Echo's SOUL is separate and unchanged.">
      <Row label="SOUL" stack>
        <textarea className="st-textarea" rows={12} value={soulDraft} onChange={e => setSoulDraft(e.target.value)} aria-label="Companion SOUL" style={{ width: "100%", fontFamily: "var(--mono, monospace)" }} />
      </Row>
      <Row label="">
        <span style={{ display: "flex", gap: 8 }}>
          <button className="es-btn es-btn-sm" disabled={!soul || soulDraft === soul.text}
            onClick={() => void send("PUT", "/companion/soul", { text: soulDraft }).then(() => setSoul(v => v && { ...v, text: soulDraft }))}>Save</button>
          <button className="es-btn es-btn-sm" disabled={!soul} onClick={() => soul && setSoulDraft(soul.default)}>Reset to default</button>
        </span>
      </Row>
    </Group>
  </>;
}
