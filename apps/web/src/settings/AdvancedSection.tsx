import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AvatarEditor } from "../components/AvatarEditor";
import { isDesktopRuntime, pickDesktopConnectionFolder } from "../desktop/bridge";
import { Group, ListEditor, Row, SecretField, Segmented, Select, Status, TextField, Toggle } from "./controls";
import type { SettingsMap } from "./useSettings";

/**
 * Settings › Advanced: settings that are still in use but rarely changed and have
 * no feature section of their own. Settings for a feature live in that feature's
 * section (voice in Voice, channel accounts in Channels, and so on).
 */

type Save = (patch: SettingsMap) => Promise<void>;
export type AdvancedPage = "settings" | "memory" | "connections" | "companion";

const asBool = (v: any) => v === true || v === "true";
const asList = (v: any): string[] => (Array.isArray(v) ? v.map(String) : String(v || "").split(/[,\n]/).map((s) => s.trim()).filter(Boolean));
const num = (v: string, fallback: number) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};

// Theme tokens (theme/tokens.css), so the preview matches the companion in light and dark.
const COMPANION_COLORS = {
  bg: "var(--es-bg-0)",
  panel: "var(--es-surface-1)",
  panel2: "var(--es-surface-2)",
  accent: "var(--es-text-strong)",
  text: "var(--es-text-strong)",
  textDim: "var(--es-text-3)",
  line: "var(--es-border)",
  danger: "var(--es-err)",
};

export function AdvancedSection(props: {
  s: SettingsMap;
  save: Save;
  apiBase: string;
  sessionId: string;
  projectId: string;
  page: AdvancedPage;
  onPage(page: AdvancedPage): void;
  onAvatarConfigChange?(config: any): void;
}) {
  return (
    <>
      <div className="st-advanced-pages">
        <Segmented
          value={props.page}
          onChange={(v) => props.onPage(v as AdvancedPage)}
          options={[
            { value: "settings", label: "Settings" },
            { value: "memory", label: "Memory & documents" },
            { value: "connections", label: "Connections & skills" },
            { value: "companion", label: "Companion" },
          ]}
        />
      </div>
      {props.page === "settings" ? <AdvancedSettings s={props.s} save={props.save} /> : null}
      {props.page === "memory" ? <MemoryPage s={props.s} save={props.save} apiBase={props.apiBase} sessionId={props.sessionId} projectId={props.projectId} /> : null}
      {props.page === "connections" ? <ConnectionsPage apiBase={props.apiBase} sessionId={props.sessionId} projectId={props.projectId} /> : null}
      {props.page === "companion" ? (
        <Group title="Echo companion" description="Echo’s shared appearance in voice conversations and the floating companion window.">
          <div className="st-embed">
            <AvatarEditor apiBase={props.apiBase} colors={COMPANION_COLORS} onConfigChange={props.onAvatarConfigChange} />
          </div>
        </Group>
      ) : null}
    </>
  );
}

// ── Settings ────────────────────────────────────────────────────────────

function AdvancedSettings({ s, save }: { s: SettingsMap; save: Save }) {
  const embedding = s.embedding || {};
  // "ollama" is the stored value for the built-in ONNX search model: memory.py uses
  // OpenAI or the local model server for those two, and the private local model for anything else.
  const embeddingOptions = [
    { value: "ollama", label: "Private search model on this PC" },
    { value: "lmstudio", label: "Local model server (LM Studio)" },
    { value: "openai", label: "OpenAI (needs an API key)" },
  ];
  const embeddingProvider = String(embedding.provider || "openai");
  return (
    <>
      <Group title="Models" description="Rarely needed. Chat models are chosen in Models.">
        <Row label="LM Studio only" help="Every chat uses LM Studio, whatever a chat had selected.">
          <Toggle checked={asBool(s.lm_studio_only)} onChange={(v) => save({ lm_studio_only: v })} label="LM Studio only" />
        </Row>
        <Row label="Memory search" help="Turns memories into vectors so they can be found by meaning. The private model is installed from Memory & documents; LM Studio needs an embedding model loaded.">
          <Select value={embeddingProvider} options={embeddingOptions} onChange={(v) => save({ embedding: { provider: v } })} />
        </Row>
        {embeddingProvider === "openai" || embeddingProvider === "lmstudio" ? (
          <Row label="Embedding model">
            <TextField mono value={embedding.model || ""} placeholder={embeddingProvider === "openai" ? "text-embedding-3-small" : "text-embedding-nomic-embed-text-v1.5"} onCommit={(v) => save({ embedding: { model: v } })} />
          </Row>
        ) : null}
      </Group>

      <Group title="Apps agents may open" description="Exact app names Echo is allowed to launch (Permissions › open applications must be on).">
        <Row label="Allowed apps" stack>
          <ListEditor items={asList(s.open_application_allowlist)} onChange={(items) => save({ open_application_allowlist: items })} placeholder="e.g. notepad" />
        </Row>
      </Group>

      <Group title="Webhooks" description="Let other apps start a routine with a signed POST to /webhooks/<path>.">
        <Row label="Enable webhooks" help="Requests must be signed with the secret below.">
          <Toggle checked={asBool(s.webhook_enabled)} onChange={(v) => save({ webhook_enabled: v })} label="Webhooks" />
        </Row>
        <Row label="Signing secret" stack><SecretField isSet={Boolean(s.webhook_secret)} onCommit={(v) => save({ webhook_secret: v })} /></Row>
        <Row label="Or read the secret from a file"><TextField mono value={s.webhook_secret_path || ""} onCommit={(v) => save({ webhook_secret_path: v })} /></Row>
      </Group>

      <Group title="Agent-to-agent (A2A)" description="Let other A2A agents send Echo tasks.">
        <Row label="Enable A2A"><Toggle checked={asBool(s.a2a_enabled)} onChange={(v) => save({ a2a_enabled: v })} label="A2A" /></Row>
        <Row label="Agent name"><TextField value={s.a2a_agent_name || ""} onCommit={(v) => save({ a2a_agent_name: v })} /></Row>
        <Row label="Description"><TextField wide value={s.a2a_agent_description || ""} onCommit={(v) => save({ a2a_agent_description: v })} /></Row>
        <Row label="Auth key" help="Required while A2A is on." stack><SecretField isSet={Boolean(s.a2a_auth_key)} onCommit={(v) => save({ a2a_auth_key: v })} /></Row>
      </Group>

      <Group title="Folders">
        <Row label="Artifacts" help="Where agents save generated files."><TextField mono value={s.artifacts_dir || ""} onCommit={(v) => save({ artifacts_dir: v })} /></Row>
        <Row label="Skills"><TextField mono value={s.skills_dir || ""} onCommit={(v) => save({ skills_dir: v })} /></Row>
        <Row label="Skill workspaces"><TextField mono value={s.workspaces_dir || ""} onCommit={(v) => save({ workspaces_dir: v })} /></Row>
        <Row label="Default workspace" help="Loads that workspace's skills at start-up. Blank: none."><TextField mono value={s.default_workspace || ""} onCommit={(v) => save({ default_workspace: v })} /></Row>
      </Group>
    </>
  );
}

// ── Memory & documents ──────────────────────────────────────────────────

type Memory = { id: string; text: string; memory_type?: string; pinned?: boolean; timestamp?: string };
const MEMORY_TYPES = ["preference", "profile", "project", "contacts", "note"];

function scope(sessionId: string, projectId: string, extra: Record<string, string> = {}) {
  const q = new URLSearchParams(extra);
  if (sessionId) q.set("thread_id", sessionId);
  q.set("project_id", projectId || "");
  return q.toString();
}

async function post(url: string, body: unknown): Promise<any> {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(data?.detail || `Request failed (${response.status})`));
  return data;
}

function MemoryPage({ s, save, apiBase, sessionId, projectId }: { s: SettingsMap; save: Save; apiBase: string; sessionId: string; projectId: string }) {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [doctor, setDoctor] = useState<any>(null);
  const [error, setError] = useState("");
  const [docs, setDocs] = useState<{ enabled: boolean; items: any[] } | null>(null);
  const [docBusy, setDocBusy] = useState(false);
  const [embeddingStatus, setEmbeddingStatus] = useState<{ runtime_available: boolean; installed: boolean; size_bytes: number } | null>(null);
  const [embeddingBusy, setEmbeddingBusy] = useState(false);
  const [embeddingRestart, setEmbeddingRestart] = useState(false);
  const [obsidian, setObsidian] = useState<{ plan: any; status: string; busy: boolean }>({ plan: null, status: "", busy: false });

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await fetch(`${apiBase}/memory?${scope(sessionId, projectId, { offset: "0", limit: "200" })}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(data?.detail || `HTTP ${response.status}`));
      setItems(Array.isArray(data.items) ? data.items : []);
    } catch (err) {
      setItems([]);
      setError(err instanceof Error ? err.message : String(err));
    }
    try {
      const response = await fetch(`${apiBase}/memory/doctor?${scope(sessionId, projectId, { max_scan: "300" })}`);
      if (response.ok) setDoctor(await response.json());
    } catch {
      setDoctor(null);
    }
    try {
      const q = new URLSearchParams({ session_id: sessionId });
      if (projectId) q.set("project_id", projectId);
      const response = await fetch(`${apiBase}/documents?${q}`);
      const data = await response.json().catch(() => ({}));
      setDocs({ enabled: Boolean(data.enabled), items: Array.isArray(data.items) ? data.items : [] });
    } catch {
      setDocs({ enabled: false, items: [] });
    }
    try {
      const response = await fetch(`${apiBase}/embeddings/local/status`);
      if (response.ok) setEmbeddingStatus(await response.json());
    } catch {
      setEmbeddingStatus(null);
    }
  }, [apiBase, sessionId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const update = (id: string, patch: Record<string, unknown>) =>
    act(() => post(`${apiBase}/memory/update`, { id, ...patch, thread_id: sessionId, project_id: projectId }));

  const shown = useMemo(() => {
    const words = query.trim().toLowerCase();
    return (items || []).filter((m) => (!filter || m.memory_type === filter) && (!words || m.text.toLowerCase().includes(words)));
  }, [items, filter, query]);
  const transcripts = useMemo(() => (items || []).filter((m) => m.memory_type === "conversation"), [items]);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setDocBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const q = new URLSearchParams({ session_id: sessionId });
      if (projectId) q.set("project_id", projectId);
      const response = await fetch(`${apiBase}/documents/upload?${q}`, { method: "POST", body: form });
      if (!response.ok) throw new Error(await response.text());
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDocBusy(false);
    }
  };

  const installEmbeddings = async () => {
    setEmbeddingBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/embeddings/local/download`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(data?.detail || `Download failed (${response.status})`));
      setEmbeddingStatus(data);
      setEmbeddingRestart(Boolean(data.restart_required));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setEmbeddingBusy(false);
    }
  };

  const obsidianPlan = async () => {
    setObsidian((o) => ({ ...o, busy: true, status: "" }));
    try {
      const q = new URLSearchParams({ session_id: sessionId, project_id: projectId });
      const response = await fetch(`${apiBase}/memory/obsidian/plan?${q}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(data?.detail || `Obsidian sync unavailable (${response.status})`));
      setObsidian({ plan: data, status: `${(data.actions || []).length} change(s) to review.`, busy: false });
    } catch (err) {
      setObsidian({ plan: null, status: err instanceof Error ? err.message : String(err), busy: false });
    }
  };
  const obsidianApply = async (direction: "export" | "import") => {
    const kinds = direction === "export" ? ["export_new", "export_update"] : ["import_new", "import_update"];
    const ids = (obsidian.plan?.actions || []).filter((a: any) => kinds.includes(String(a.kind))).map((a: any) => String(a.id));
    if (!ids.length) return;
    setObsidian((o) => ({ ...o, busy: true }));
    try {
      await post(`${apiBase}/memory/obsidian/apply`, { session_id: sessionId, project_id: projectId, direction, action_ids: ids });
      await load();
      await obsidianPlan();
    } catch (err) {
      setObsidian((o) => ({ ...o, busy: false, status: err instanceof Error ? err.message : String(err) }));
    }
  };

  return (
    <>
      <Group
        title="Saved memories"
        description="Facts Echo keeps about you. Pinned ones are always recalled."
        action={<button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => void load()}>Refresh</button>}
      >
        <Row label={<Status tone={doctor ? (doctor.ok ? "ok" : "warn") : "idle"}>{doctor ? (doctor.ok ? "Healthy" : "Needs review") : "Checking…"}</Status>}
          help={items ? `${items.length} memories · ${items.filter((m) => m.pinned).length} pinned${doctor?.duplicate_groups?.length ? ` · ${doctor.duplicate_groups.length} duplicate group(s)` : ""}${doctor?.warnings?.length ? ` · ${doctor.warnings[0]}` : ""}` : undefined}>
          <button type="button" className="es-btn es-btn-sm" disabled={!items?.length} onClick={() => void act(() => post(`${apiBase}/memory/compact?${scope(sessionId, projectId)}`, {}))}>Merge duplicates</button>
          {/* Older versions copied every chat turn into memory; those transcripts aren't facts. */}
          {transcripts.length ? (
            <button type="button" className="es-btn es-btn-sm" title="Chats stay in your history and chat search; this only removes the copies kept as memories."
              onClick={() => window.confirm(`Remove ${transcripts.length} saved chat transcript${transcripts.length === 1 ? "" : "s"} from memory? Your chats themselves are kept.`)
                && void act(() => post(`${apiBase}/memory/delete`, { ids: transcripts.map((m) => m.id), thread_id: sessionId, project_id: projectId }))}>
              Remove {transcripts.length} chat transcript{transcripts.length === 1 ? "" : "s"}
            </button>
          ) : null}
          <button type="button" className="es-btn es-btn-sm es-btn-quiet" disabled={!items?.length}
            onClick={() => window.confirm("Delete every saved memory?") && void act(() => post(`${apiBase}/memory/clear?${scope(sessionId, projectId)}`, {}))}>
            Clear all
          </button>
        </Row>
        <Row label="Find">
          <TextField value={query} placeholder="Search memories" onCommit={setQuery} />
          <Select value={filter} onChange={setFilter} options={[{ value: "", label: "All types" }, ...MEMORY_TYPES.map((t) => ({ value: t, label: t[0].toUpperCase() + t.slice(1) }))]} />
        </Row>
        {error ? <Row label={<Status tone="err">{error}</Status>} /> : null}
        {items === null ? <Row label="Loading…" /> : null}
        {items !== null && !shown.length ? <Row label={<span className="st-muted">{items.length ? "No memories match." : "No saved memories yet."}</span>} /> : null}
        {shown.slice(0, 200).map((m) => (
          <Row key={m.id} label={<MemoryText memory={m} onSave={(text) => update(m.id, { text })} />} help={[m.memory_type || "untyped", m.pinned ? "pinned" : "", m.timestamp ? new Date(m.timestamp).toLocaleDateString() : ""].filter(Boolean).join(" · ")}>
            <Select value={m.memory_type || ""} onChange={(v) => update(m.id, { memory_type: v })} options={[{ value: "", label: "Type" }, ...MEMORY_TYPES.map((t) => ({ value: t, label: t }))]} />
            <Toggle checked={Boolean(m.pinned)} onChange={(v) => update(m.id, { pinned: v })} label="Pinned" />
            <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => void act(() => post(`${apiBase}/memory/delete`, { ids: [m.id], thread_id: sessionId, project_id: projectId }))}>Delete</button>
          </Row>
        ))}
      </Group>

      <Group title="Local search model" description="Optional ONNX model for private memory and document search. Model-server embeddings can be used instead.">
        <Row label={<Status tone={embeddingStatus?.installed ? "ok" : "idle"}>{embeddingStatus?.installed ? "Installed" : "Not installed"}</Status>}
          help={embeddingRestart ? "Restart EchoSpeak to use the newly installed model." : "About 90 MB; downloaded only when you choose Install."}>
          {!embeddingStatus?.installed ? <button type="button" className="es-btn es-btn-sm" disabled={!embeddingStatus?.runtime_available || embeddingBusy}
            onClick={() => void installEmbeddings()}>{embeddingBusy ? "Downloading…" : "Install"}</button> : null}
        </Row>
      </Group>

      <Group title="Documents" description="Files agents can search when answering. Turn document search on in Memory.">
        {docs && !docs.enabled ? <Row label={<span className="st-muted">Document search is off.</span>} /> : null}
        {docs?.enabled ? (
          <Row label="Upload a document" help="PDF, Word, text or Markdown.">
            <label className="es-btn es-btn-sm">
              {docBusy ? "Uploading…" : "Choose file"}
              <input type="file" hidden disabled={docBusy} onChange={(e) => void upload(e.target.files?.[0])} />
            </label>
          </Row>
        ) : null}
        {(docs?.items || []).map((doc: any) => (
          <Row key={doc.id} label={doc.filename} help={`${doc.chunks} passages`}>
            <button type="button" className="es-btn es-btn-sm es-btn-quiet"
              onClick={() => void act(() => post(`${apiBase}/documents/delete`, { ids: [doc.id], session_id: sessionId, project_id: projectId || "" }))}>
              Delete
            </button>
          </Row>
        ))}
      </Group>

      <Group title="Document search settings" description="How uploaded documents are searched. Turn document search on in Memory.">
        <Row label="Rerank results"><Toggle checked={asBool(s.doc_rerank_enabled)} onChange={(v) => save({ doc_rerank_enabled: v })} label="Rerank" /></Row>
        <Row label="Graph expansion" help="Also pull in passages about related names."><Toggle checked={asBool(s.doc_graph_enabled)} onChange={(v) => save({ doc_graph_enabled: v })} label="Graph expansion" /></Row>
        <Row label="Largest upload (MB)">
          <TextField type="number" value={s.doc_upload_max_mb ?? 25} onCommit={(v) => save({ doc_upload_max_mb: num(v, 25) })} />
        </Row>
        <Row label="Context per answer (characters)">
          <TextField type="number" value={s.doc_context_max_chars ?? 6000} onCommit={(v) => save({ doc_context_max_chars: num(v, 6000) })} />
        </Row>
      </Group>

      <Group title="Obsidian sync" description="Optional: copy memories to and from an Obsidian vault connected to this project. EchoSpeak's memory stays the source of truth.">
        {projectId ? (
          <Row label={obsidian.status || "Check what would change first."}>
            <button type="button" className="es-btn es-btn-sm" disabled={obsidian.busy} onClick={() => void obsidianPlan()}>Check</button>
            <button type="button" className="es-btn es-btn-sm es-btn-quiet" disabled={obsidian.busy || !obsidian.plan} onClick={() => void obsidianApply("export")}>Export</button>
            <button type="button" className="es-btn es-btn-sm es-btn-quiet" disabled={obsidian.busy || !obsidian.plan} onClick={() => void obsidianApply("import")}>Import</button>
          </Row>
        ) : (
          <Row label={<span className="st-muted">Open a chat in a project with a connected vault to sync.</span>} />
        )}
      </Group>
    </>
  );
}

function MemoryText({ memory, onSave }: { memory: Memory; onSave(text: string): void }) {
  const [editing, setEditing] = useState(false);
  if (!editing) {
    return (
      <button type="button" className="st-memory-text" title="Edit" onClick={() => setEditing(true)}>
        {memory.text}
      </button>
    );
  }
  return (
    <TextField
      wide
      value={memory.text}
      onCommit={(text) => {
        setEditing(false);
        if (text.trim() && text !== memory.text) onSave(text.trim());
      }}
    />
  );
}

// ── Connections, skills, MCP ────────────────────────────────────────────

function ConnectionsPage({ apiBase, sessionId, projectId }: { apiBase: string; sessionId: string; projectId: string }) {
  const [cards, setCards] = useState<any[] | null>(null);
  const [providers, setProviders] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    if (!sessionId) {
      setCards([]);
      return;
    }
    try {
      const q = new URLSearchParams({ session_id: sessionId, project_id: projectId });
      const [catalog, connections] = await Promise.all([
        fetch(`${apiBase}/settings/catalog?${q}`).then((r) => r.json()),
        fetch(`${apiBase}/connections/catalog?${q}`).then((r) => r.json()),
      ]);
      setCards(Array.isArray(catalog.cards) ? catalog.cards : []);
      setProviders(Array.isArray(connections.items) ? connections.items : []);
      setError("");
    } catch (err) {
      setCards([]);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase, sessionId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError("");
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const connectionAction = (provider: any, action: "connect" | "probe" | "reconnect" | "disable" | "disconnect") =>
    run(String(provider?.connection?.id || provider?.id), async () => {
      const connection = provider.connection;
      const body = { session_id: sessionId, project_id: projectId, expected_revision: connection?.revision };
      if (action === "connect") {
        if (provider.id !== "obsidian") throw new Error(`${provider.name} sign-in isn't available yet.`);
        if (!isDesktopRuntime()) throw new Error("Connect local folders from the EchoSpeak desktop app.");
        const vaultPath = await pickDesktopConnectionFolder(provider.name);
        if (!vaultPath) return;
        return post(`${apiBase}/connections/authorize`, {
          provider_id: provider.id, session_id: sessionId, project_id: projectId, display_name: provider.name,
          configuration: { vault_path: vaultPath }, credentials: {}, allow_global: false,
        });
      }
      if (action === "disconnect") {
        const q = new URLSearchParams({ session_id: sessionId, project_id: projectId, expected_revision: String(connection.revision) });
        const response = await fetch(`${apiBase}/connections/${encodeURIComponent(connection.id)}?${q}`, { method: "DELETE" });
        if (!response.ok) throw new Error(`Disconnect failed (${response.status})`);
        return;
      }
      return post(`${apiBase}/connections/${encodeURIComponent(connection.id)}/${action}`, body);
    });

  const toggleCapability = (connection: any, capability: any) =>
    run(String(connection.id), async () => {
      const response = await fetch(`${apiBase}/connections/${encodeURIComponent(connection.id)}/capabilities/${encodeURIComponent(capability.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, project_id: projectId, expected_revision: connection.revision, enabled: !capability.enabled }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(data?.detail || `Update failed (${response.status})`));
    });

  const byCategory = (category: string) => (cards || []).filter((c) => c.category === category);
  const tone = (card: any): "ok" | "warn" | "err" | "idle" =>
    card.ready ? "ok" : card.status === "disabled" ? "idle" : card.issue ? "warn" : "idle";

  return (
    <>
      {error ? <Group><Row label={<Status tone="err">{error}</Status>} /></Group> : null}
      <Group
        title="Connections"
        description={projectId ? "Accounts and apps agents can use in this project." : "Open a chat in a project to connect accounts and apps to it."}
        action={<button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => void load()}>Refresh</button>}
      >
        {cards === null ? <Row label="Loading…" /> : null}
        {byCategory("connections").map((card) => {
          const provider = providers.find((p) => `connection:${p.id}` === card.id || p.id === card.id);
          const connection = provider?.connection;
          const key = String(connection?.id || provider?.id || card.id);
          const reconnect = card.status === "reconnect_required" || connection?.authentication === "expired";
          return (
            <React.Fragment key={card.id}>
              <Row label={<>{card.name} <Status tone={tone(card)}>{card.status_label}</Status></>} help={card.issue || card.description}>
                {connection && projectId ? (
                  <>
                    <button type="button" className="es-btn es-btn-sm" disabled={busy === key} onClick={() => void connectionAction(provider, reconnect ? "reconnect" : "probe")}>{reconnect ? "Reconnect" : "Check"}</button>
                    <button type="button" className="es-btn es-btn-sm es-btn-quiet" disabled={busy === key} onClick={() => void connectionAction(provider, connection.enabled ? "disable" : "reconnect")}>{connection.enabled ? "Disable" : "Enable"}</button>
                    <button type="button" className="es-btn es-btn-sm es-btn-quiet" disabled={busy === key}
                      onClick={() => window.confirm(`Disconnect ${card.name}?`) && void connectionAction(provider, "disconnect")}>Disconnect</button>
                  </>
                ) : provider?.id === "obsidian" && projectId ? (
                  <button type="button" className="es-btn es-btn-sm" disabled={busy === key} onClick={() => void connectionAction(provider, "connect")}>Connect</button>
                ) : null}
              </Row>
              {connection && projectId
                ? (card.capabilities || []).map((capability: any) => (
                    <Row key={`${card.id}:${capability.id}`} label={<span className="st-indent">{capability.label || capability.id}</span>} help={capability.access}>
                      <Toggle checked={Boolean(capability.enabled)} disabled={busy === key || capability.available === false} onChange={() => void toggleCapability(connection, capability)} label={capability.label || capability.id} />
                    </Row>
                  ))
                : null}
            </React.Fragment>
          );
        })}
      </Group>
      <Group title="Skills" description="Installed workflow packages and whether the tools they need are reachable.">
        {byCategory("skills").map((card) => (
          <Row key={card.id} label={<>{card.name} <Status tone={tone(card)}>{card.status_label}</Status></>} help={card.issue || `${card.capabilities?.length || 0} tools`} />
        ))}
      </Group>
      <Group title="MCP servers" description="Servers listed in settings.json under mcp_servers, and the tools they provide.">
        {byCategory("mcp").map((card) => (
          <Row key={card.id} label={<>{card.name} <Status tone={tone(card)}>{card.status_label}</Status></>} help={card.issue || card.detail} />
        ))}
      </Group>
    </>
  );
}
