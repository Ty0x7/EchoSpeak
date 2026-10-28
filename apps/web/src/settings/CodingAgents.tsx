import React, { useCallback, useEffect, useState } from "react";
import { RuntimeMark } from "../components/RuntimeMark";
import { isDesktopRuntime, openDesktopExternalUrl } from "../desktop/bridge";
import { request } from "../projects/model";
import type { RuntimeCatalog } from "../code/model";
import { Group, Row, Status, TextField } from "./controls";
import type { SettingsMap } from "./useSettings";

type Save = (patch: SettingsMap) => Promise<void>;
export type Account = {
  installed: boolean; source?: string; problem?: string; signed_in: boolean;
  method?: string; plan?: string; models?: unknown[]; login_open?: boolean;
};

const AGENTS = [
  {
    id: "codex", name: "Codex", pathKey: "codex_executable", placeholder: "C:\\…\\codex.exe",
    about: "OpenAI's Codex in its own app-server, with your ChatGPT sign-in and plan. Found automatically when the Codex app or CLI is installed.",
  },
  {
    id: "claude_cli", name: "Claude Code", pathKey: "claude_code_executable", placeholder: "C:\\Users\\you\\.local\\bin\\claude.exe",
    about: "Your installed Claude Code, signed in with your own Claude plan through Claude Code's own login. EchoSpeak runs it unmodified and never sees your sign-in.",
  },
] as const;

export function accountLine(account: Account, agent: string): string {
  if (!account.signed_in) return "Not signed in";
  if (agent === "codex") return `Signed in with ChatGPT${account.plan ? ` · ${account.plan[0].toUpperCase()}${account.plan.slice(1)} plan` : ""}`;
  if (account.method === "claude.ai") return `Signed in with your Claude ${account.plan ? `${account.plan[0].toUpperCase()}${account.plan.slice(1)} ` : ""}plan`;
  return `Signed in (${account.method || "Claude Code"})`;
}

async function openUrl(url: string) {
  if (isDesktopRuntime()) await openDesktopExternalUrl(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

/** Settings › Models › Coding agents: each agent's own harness and sign-in, checked only when you ask. */
export function CodingAgents({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const [catalog, setCatalog] = useState<RuntimeCatalog | null>(null);
  const [accounts, setAccounts] = useState<Record<string, Account>>({});
  const [busy, setBusy] = useState("");
  const [notes, setNotes] = useState<Record<string, { tone: "ok" | "warn" | "err"; text: string }>>({});

  const load = useCallback(() => request<RuntimeCatalog>(`${apiBase}/echo-code/runtimes`).then(setCatalog).catch(() => setCatalog(null)), [apiBase]);
  useEffect(() => { void load(); }, [load]);
  const describe = (agent: string, account: Account) => account.installed
    ? { tone: account.signed_in ? "ok" as const : "warn" as const, text: accountLine(account, agent) + (account.models?.length ? ` · ${account.models.length} models` : "") }
    : { tone: "err" as const, text: account.problem || "Not found" };
  // What Echo last saw, without running anything: shows "signed in" right after a restart.
  useEffect(() => {
    let live = true;
    for (const agent of AGENTS) {
      request<Account & { checked_at?: number }>(`${apiBase}/echo-code/runtimes/${agent.id}/account`).then(account => {
        if (!live || !account.checked_at) return;
        setAccounts(a => ({ ...a, [agent.id]: account }));
        setNotes(n => ({ ...n, [agent.id]: describe(agent.id, account) }));
      }).catch(() => undefined);
    }
    return () => { live = false; };
  }, [apiBase]);

  const run = async (agent: string, work: () => Promise<void>) => {
    setBusy(agent);
    try { await work(); }
    catch (err) { setNotes(n => ({ ...n, [agent]: { tone: "err", text: err instanceof Error ? err.message : "Request failed" } })); }
    finally { setBusy(""); }
  };
  const check = (agent: string) => run(agent, async () => {
    const account = await request<Account>(`${apiBase}/echo-code/runtimes/${agent}/check`, {});
    setAccounts(a => ({ ...a, [agent]: account }));
    setNotes(n => ({ ...n, [agent]: describe(agent, account) }));
  });
  const signIn = (agent: string) => run(agent, async () => {
    const result = await request<{ url?: string; started?: boolean }>(`${apiBase}/echo-code/runtimes/${agent}/login`, {});
    if (result.url) await openUrl(result.url);
    setNotes(n => ({ ...n, [agent]: { tone: "warn", text: agent === "codex"
      ? "Finish signing in on the ChatGPT page that opened, then choose Check."
      : "Finish Claude Code's sign-in in the window that opened, then choose Check." } }));
  });
  const savePath = (key: string, value: string) => run(key, async () => { await save({ [key]: value.trim() }); await load(); });

  const row = (id: string) => catalog?.runtimes.find(r => r.id === id);
  const blocked = catalog && catalog.privacy_mode !== "standard";
  return (
    <Group title="Coding agents" description="Plans for code: Echo Code hands a task to Codex (your ChatGPT plan) or Claude Code (your Claude plan) in an isolated worktree. Each runs in its own harness with its own sign-in and tools; Echo asks you before anything they would ask about.">
      {blocked ? <div className="st-muted" role="status">Private and Offline modes block coding agents, because their network use can't be limited.</div> : null}
      {AGENTS.map(agent => {
        const runtime = row(agent.id);
        const account = accounts[agent.id];
        const note = notes[agent.id];
        const found = runtime?.installed === true;
        return (
          <React.Fragment key={agent.id}>
            <Row label={<span className="st-agent-label"><RuntimeMark runtime={agent.id} /><span><strong>{agent.name}</strong>
              <small>{!catalog ? "Checking…" : found ? "Installed" : "Not found"}</small></span></span>} help={agent.about}>
              <button type="button" className="es-btn es-btn-sm" disabled={!found || Boolean(busy) || Boolean(blocked)} onClick={() => void check(agent.id)}>
                {busy === agent.id ? "Checking…" : "Check"}</button>
              {/* Only once a check shows it's signed out: a second sign-in never replaces a working one. */}
              <button type="button" className="es-btn es-btn-sm" disabled={!found || Boolean(busy) || Boolean(blocked) || account?.signed_in !== false}
                title={account?.signed_in === false ? undefined : "Check first; Sign in is offered when the agent isn't signed in"}
                onClick={() => void signIn(agent.id)}>Sign in</button>
            </Row>
            {note ? <Row label="" help={<Status tone={note.tone}>{note.text}</Status>} /> : null}
            {runtime && !found && runtime.blockers.length ? <Row label="" help={<Status tone="err">{runtime.blockers[0]}</Status>} /> : null}
            <Row label={`${agent.name} location`} help="Leave empty to find it automatically. Set it when it's installed somewhere else.">
              <TextField value={String(s[agent.pathKey] || "")} placeholder={agent.placeholder} mono wide onCommit={v => void savePath(agent.pathKey, v)} />
            </Row>
          </React.Fragment>
        );
      })}
      <Row label={<span className="st-agent-label"><RuntimeMark runtime="claude_cli" /><span><strong>ChatGPT and Claude by API key</strong><small>In Chat</small></span></span>}
        help="API keys are for Chat, where Echo's own harness calls the model. Pick ChatGPT or Claude in Chat's model picker, or add keys under Cloud model above. Echo Code uses your plans instead." />
      <Row label={<span className="st-agent-label"><RuntimeMark runtime="acp" /><span><strong>Other ACP agents</strong><small>Per session</small></span></span>}
        help="Choose the agent's executable when you start a session in Echo Code." />
    </Group>
  );
}
