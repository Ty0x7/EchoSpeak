import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { announceUpdate } from "../dashboard/useDesktopUpdate";
import { AgentAvatar } from "../lean/LeanMessage";
import type { LeanPersona } from "../lean/types";
import {
  checkForDesktopUpdate,
  installDesktopUpdate,
  isDesktopRuntime,
  onDesktopUpdateProgress,
  type DesktopUpdateInfo,
  type DesktopUpdateProgress,
} from "../desktop/bridge";
import { ChoiceCards, Group, ListEditor, Row, SecretField, Segmented, Select, Status, TextField, Toggle } from "./controls";
import { useSettings, type SettingsMap } from "./useSettings";
import { AdvancedSection, type AdvancedPage } from "./AdvancedSection";

type SectionId =
  | "general"
  | "models"
  | "agents"
  | "personality"
  | "permissions"
  | "terminal"
  | "voice"
  | "search"
  | "memory"
  | "automations"
  | "channels"
  | "advanced"
  | "about";

const NAV: { group: string; items: { id: SectionId; label: string; icon: IconName }[] }[] = [
  {
    group: "",
    items: [
      { id: "general", label: "General", icon: "sliders" },
      { id: "models", label: "Models", icon: "chip" },
      { id: "agents", label: "Agents", icon: "people" },
      { id: "personality", label: "Personality", icon: "spark" },
    ],
  },
  {
    group: "Capabilities",
    items: [
      { id: "permissions", label: "Permissions", icon: "shield" },
      { id: "terminal", label: "Terminal", icon: "terminal" },
      { id: "voice", label: "Voice", icon: "mic" },
      { id: "search", label: "Web search", icon: "globe" },
      { id: "memory", label: "Memory", icon: "brain" },
    ],
  },
  {
    group: "Automation",
    items: [
      { id: "automations", label: "Automations", icon: "clock" },
      { id: "channels", label: "Channels", icon: "send" },
    ],
  },
  {
    group: "",
    items: [
      { id: "advanced", label: "Advanced", icon: "wrench" },
      { id: "about", label: "About", icon: "info" },
    ],
  },
];

type IconName = "sliders" | "chip" | "people" | "spark" | "shield" | "terminal" | "mic" | "globe" | "brain" | "clock" | "send" | "info" | "wrench";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    sliders: <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4" />,
    chip: <><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></>,
    people: <><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5" /><circle cx="17" cy="9" r="2.4" /><path d="M16 14.6c2.2.2 3.8 1.6 4.5 4.4" /></>,
    spark: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" />,
    shield: <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z M9 12l2 2 4-4" />,
    terminal: <><rect x="3" y="4.5" width="18" height="15" rx="2.5" /><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4" /></>,
    mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></>,
    globe: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.4 2.4 3.6 5.2 3.6 8.5s-1.2 6.1-3.6 8.5c-2.4-2.4-3.6-5.2-3.6-8.5S9.6 5.9 12 3.5z" /></>,
    brain: <path d="M9 4.5a3 3 0 0 0-3 3v.3A3 3 0 0 0 4.5 13a3 3 0 0 0 2 4.5A3 3 0 0 0 12 18V5.5A1.5 1.5 0 0 0 10.5 4H9zM15 4.5a3 3 0 0 1 3 3v.3a3 3 0 0 1 1.5 5.2 3 3 0 0 1-2 4.5A3 3 0 0 1 12 18" />,
    clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
    send: <path d="M4 12l16-8-6 16-2.5-6.5z M11.5 13.5L20 4" />,
    info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h0" /></>,
    wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L4 16.8V20h3.2l5.3-5.3a4 4 0 0 0 5.2-5.4l-2.4 2.4-2.6-.6-.6-2.6z" />,
  };
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {paths[name]}
    </svg>
  );
}

const asBool = (v: any) => v === true || v === "true";
const asList = (v: any): string[] => (Array.isArray(v) ? v.map(String) : String(v || "").split(/[,\n]/).map((s) => s.trim()).filter(Boolean));

export type SettingsPanelProps = {
  apiBase: string;
  fullscreen: boolean;
  agents: LeanPersona[];
  onClose(): void;
  onEditAgent(agent: LeanPersona | null): void;
  /** The chat that was open, for pages scoped to a session or project (memory, connections). */
  sessionId?: string;
  projectId?: string;
  onAvatarConfigChange?(config: any): void;
};

export function SettingsPanel(props: SettingsPanelProps) {
  const { settings, error, save, saveState, saveError, reload } = useSettings(props.apiBase);
  const [section, setSection] = useState<SectionId>(() => {
    try {
      return (localStorage.getItem("echospeak.settings.section") as SectionId) || "general";
    } catch {
      return "general";
    }
  });
  const [query, setQuery] = useState("");
  useEffect(() => {
    const navigate = (event: Event) => {
      const target = (event as CustomEvent<string>).detail;
      if (target === "about" || target === "voice") setSection(target);
    };
    const storage = (event: StorageEvent) => {
      if (event.key === "echospeak.settings.section" && (event.newValue === "about" || event.newValue === "voice")) setSection(event.newValue);
    };
    window.addEventListener("echospeak.settings.navigate", navigate);
    window.addEventListener("storage", storage);
    return () => { window.removeEventListener("echospeak.settings.navigate", navigate); window.removeEventListener("storage", storage); };
  }, []);
  const [advancedPage, setAdvancedPage] = useState<AdvancedPage>("settings");
  const openAdvanced = useCallback((page: AdvancedPage) => {
    setAdvancedPage(page);
    setSection("advanced");
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem("echospeak.settings.section", section);
    } catch {
      // Storage can be unavailable; the default section is fine.
    }
  }, [section]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") props.onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.onClose]);

  const nav = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return NAV;
    return NAV.map((g) => ({ ...g, items: g.items.filter((i) => i.label.toLowerCase().includes(q) || i.id.includes(q)) })).filter((g) => g.items.length);
  }, [query]);
  const current = NAV.flatMap((g) => g.items).find((i) => i.id === section);

  const body = !settings ? (
    <div className="st-empty">{error ? `Couldn't load settings: ${error}` : "Loading…"}</div>
  ) : (
    <SectionBody
      id={section}
      s={settings}
      save={save}
      props={props}
      reload={reload}
      openAdvanced={openAdvanced}
      advancedPage={advancedPage}
      setAdvancedPage={setAdvancedPage}
    />
  );

  return (
    <div className={`st-root${props.fullscreen ? " is-fullscreen" : ""}`} onMouseDown={(e) => e.target === e.currentTarget && !props.fullscreen && props.onClose()}>
      <div className="st-window" role="dialog" aria-modal="true" aria-label="Settings">
        <aside className="st-nav">
          <div className="st-nav-head">
            <h2>Settings</h2>
          </div>
          <div className="st-search">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search settings" aria-label="Search settings" />
          </div>
          <nav className="st-nav-list">
            {nav.map((group, gi) => (
              <div key={`${group.group}-${gi}`} className="st-nav-group">
                {group.group ? <div className="st-nav-label">{group.group}</div> : null}
                {group.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`st-nav-item${section === item.id ? " is-active" : ""}`}
                    onClick={() => setSection(item.id)}
                    aria-current={section === item.id ? "page" : undefined}
                  >
                    <Icon name={item.icon} />
                    <span>{item.label}</span>
                  </button>
                ))}
              </div>
            ))}
          </nav>
        </aside>
        <main className="st-main">
          <header className="st-main-head">
            <h1>{current?.label || "Settings"}</h1>
            <div className="st-head-right">
              <span className="st-save" data-state={saveState} title={saveError || undefined}>
                {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : saveState === "error" ? "Couldn't save" : ""}
              </span>
              <button type="button" className="es-icon-btn st-close" aria-label="Close settings" onClick={props.onClose}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </button>
            </div>
          </header>
          <div className="st-scroll">{body}</div>
        </main>
      </div>
    </div>
  );
}

type Save = (patch: SettingsMap) => Promise<void>;

function SectionBody({
  id,
  s,
  save,
  props,
  reload,
  openAdvanced,
  advancedPage,
  setAdvancedPage,
}: {
  id: SectionId;
  s: SettingsMap;
  save: Save;
  props: SettingsPanelProps;
  reload(): Promise<void>;
  openAdvanced(page: AdvancedPage): void;
  advancedPage: AdvancedPage;
  setAdvancedPage(page: AdvancedPage): void;
}) {
  switch (id) {
    case "general":
      return <GeneralSection s={s} save={save} />;
    case "models":
      return <ModelsSection s={s} save={save} apiBase={props.apiBase} />;
    case "agents":
      return <AgentsSection agents={props.agents} onEdit={props.onEditAgent} />;
    case "personality":
      return <PersonalitySection apiBase={props.apiBase} />;
    case "permissions":
      return <PermissionsSection s={s} save={save} />;
    case "terminal":
      return <TerminalSection s={s} save={save} apiBase={props.apiBase} />;
    case "voice":
      return <VoiceSection s={s} save={save} apiBase={props.apiBase} openAdvanced={openAdvanced} />;
    case "search":
      return <SearchSection s={s} save={save} />;
    case "memory":
      return <MemorySection s={s} save={save} openAdvanced={openAdvanced} />;
    case "automations":
      return <AutomationsSection s={s} save={save} apiBase={props.apiBase} agents={props.agents} />;
    case "channels":
      return <ChannelsSection s={s} save={save} />;
    case "advanced":
      return (
        <AdvancedSection
          s={s}
          save={save}
          apiBase={props.apiBase}
          sessionId={props.sessionId || ""}
          projectId={props.projectId || ""}
          page={advancedPage}
          onPage={setAdvancedPage}
          onAvatarConfigChange={props.onAvatarConfigChange}
        />
      );
    case "about":
      return <AboutSection apiBase={props.apiBase} openAdvanced={openAdvanced} reload={reload} />;
  }
}

// ── Voice ───────────────────────────────────────────────────────────────
type VoiceSetupInfo = {
  runtime_available: boolean;
  provider: string;
  models: { size: string; label: string; mb: number; note: string; installed: boolean; active: boolean }[];
  download: { running: boolean; size: string; progress: number; error: string };
};

export function VoiceSection({ s, save, apiBase, openAdvanced }: { s: SettingsMap; save: Save; apiBase: string; openAdvanced(page: AdvancedPage): void }) {
  const [info, setInfo] = useState<VoiceSetupInfo | null>(null);
  const refresh = useCallback(async () => {
    try {
      setInfo(await (await fetch(`${apiBase}/media-runtime/voice/setup`)).json());
    } catch {
      setInfo(null);
    }
  }, [apiBase]);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), info?.download?.running ? 1000 : 6000);
    return () => window.clearInterval(timer);
  }, [refresh, info?.download?.running]);

  const install = async (size: string) => {
    const response = await fetch(`${apiBase}/media-runtime/voice/setup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ size }),
    });
    if (response.ok) setInfo(await response.json());
  };

  const active = info?.models.find((m) => m.active);
  const usingWhisper = info?.provider === "faster-whisper-local" && Boolean(active);
  let tone: "ok" | "warn" | "err" | "idle" = "idle";
  let statusText = "Checking…";
  if (info) {
    if (!info.runtime_available) {
      tone = "err";
      statusText = "The speech engine isn't included in this build";
    } else if (info.download.running) {
      tone = "warn";
      statusText = `Downloading ${info.download.size} model · ${Math.round(info.download.progress * 100)}%`;
    } else if (usingWhisper) {
      tone = "ok";
      statusText = `Ready · Whisper ${active!.label} on this PC`;
    } else {
      tone = "warn";
      statusText = "Not set up · the mic uses Windows speech recognition";
    }
  }

  return (
    <>
      <Group title="Speech to text" description="Pick a Whisper model to download. It runs on this PC, so what you say never leaves it.">
        <Row label={<Status tone={tone}>{statusText}</Status>} help={info?.download.error || undefined} />
        {info?.models.map((m) => (
          <Row key={m.size} label={`${m.label} · ${m.mb} MB`} help={m.note}>
            {m.active ? (
              <span className="st-muted">In use</span>
            ) : (
              <button
                type="button"
                className={`es-btn es-btn-sm${m.size === "base" && !usingWhisper ? " es-btn-primary" : ""}`}
                disabled={!info.runtime_available || info.download.running}
                onClick={() => void install(m.size)}
              >
                {m.installed ? "Use" : "Download"}
              </button>
            )}
          </Row>
        ))}
      </Group>
      <Group title="Wake word" description="Say “Hey Echo” to start talking without touching the keyboard. Turn it on with Wake in the composer.">
        <Row label="Wake word" help={usingWhisper ? "Listens for short bursts of speech and checks them on this PC." : "Set up speech to text above first."}>
          <TextField value={String(s.voice_wake_word || "echo")} onCommit={(v: string) => save({ voice_wake_word: v.trim().toLowerCase() || "echo" })} />
        </Row>
      </Group>
      <Group title="More">
        <Row label="Read-aloud, whisper.cpp and cloud voice providers">
          <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => openAdvanced("settings")}>Open</button>
        </Row>
      </Group>
    </>
  );
}

// ── General ─────────────────────────────────────────────────────────────
function GeneralSection({ s, save }: { s: SettingsMap; save: Save }) {
  return (
    <>
      <Group>
        <Row label="Setup" help="Revisit the first-time model and capability checklist."><button className="es-btn es-btn-sm" onClick={() => window.dispatchEvent(new Event("echospeak:open-setup"))}>Open setup</button></Row>
        <Row label="Your name" help="How your agents refer to you.">
          <TextField value={s.user_display_name || ""} placeholder="Ty" onCommit={(v) => save({ user_display_name: v })} />
        </Row>
      </Group>
      <Group title="How agents work" description="These apply to Echo and every agent you create.">
        <Row label="Ask before risky actions" help="Deleting files, sending messages, controlling the desktop, and dangerous commands.">
          <Segmented
            value={String(s.lean_approval_mode || "smart")}
            onChange={(v) => save({ lean_approval_mode: v })}
            options={[
              { value: "smart", label: "Risky only", hint: "Recommended" },
              { value: "always", label: "Every action" },
              { value: "never", label: "Never" },
            ]}
          />
        </Row>
        <Row label="Step budget" help="Most model steps an agent may take on one message before it wraps up. Higher lets it finish bigger jobs.">
          <Select
            value={String(s.lean_max_iterations || 60)}
            onChange={(v) => save({ lean_max_iterations: Number(v) })}
            options={[20, 40, 60, 100, 150, 250].map((n) => ({ value: String(n), label: `${n} steps` }))}
          />
        </Row>
      </Group>
    </>
  );
}

// ── Models ──────────────────────────────────────────────────────────────
const LOCAL_PROVIDERS = [
  { value: "lmstudio", label: "LM Studio" },
  { value: "ollama", label: "Ollama" },
  { value: "localai", label: "LocalAI" },
  { value: "vllm", label: "vLLM" },
];

const LOCAL_DEFAULT_URLS: Record<string, string> = {
  lmstudio: "http://localhost:1234",
  ollama: "http://localhost:11434",
  localai: "http://localhost:8080",
  vllm: "http://localhost:8000",
};

type DetectRow = { provider: string; base_url: string; running: boolean; models: string[] };

export function ModelsSection({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const local = s.local || {};
  const useLocal = asBool(s.use_local_models);
  const provider = useLocal ? String(local.provider || "lmstudio") : String(s.default_cloud_provider || "openai");
  const providerLabel = LOCAL_PROVIDERS.find((p) => p.value === provider)?.label || provider;
  const [models, setModels] = useState<string[] | null>(null);
  const [catalog, setCatalog] = useState<{ id: string; name: string; chat: boolean; live: boolean; reason: string }[]>([]);
  const [catalogMessage, setCatalogMessage] = useState("");
  const [detected, setDetected] = useState<DetectRow[] | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [test, setTest] = useState<{ busy: boolean; ok?: boolean; message?: string }>({ busy: false });
  const [reloadKey, setReloadKey] = useState(0);
  // Once the user picks an app themselves, never switch it for them.
  const userPicked = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setModels(null);
    setCatalog([]);
    setCatalogMessage("");
    fetch(`${apiBase}/provider/models?provider=${encodeURIComponent(provider)}`)
      .then((r) => { if (!r.ok) throw new Error(`Unable to load models (HTTP ${r.status})`); return r.json(); })
      .then((d) => { if (!cancelled) { setModels(Array.isArray(d.models) ? d.models.map(String) : []); setCatalog(Array.isArray(d.catalog) ? d.catalog : []); setCatalogMessage(String(d.message || "")); } })
      .catch((err) => { if (!cancelled) { setModels([]); setCatalogMessage(String(err)); } });
    return () => {
      cancelled = true;
    };
  }, [apiBase, provider, local.base_url, reloadKey]);

  useEffect(() => {
    const onSaved = (event: Event) => {
      const patch = (event as CustomEvent).detail || {};
      if (patch[provider] || patch.default_cloud_provider !== undefined || patch.use_local_models !== undefined) {
        setReloadKey((k) => k + 1);
        setTest({ busy: false });
      }
    };
    window.addEventListener("echospeak:settings-saved", onSaved);
    return () => window.removeEventListener("echospeak:settings-saved", onSaved);
  }, [provider]);

  // A model that this app doesn't have can't work: pick the first one it does have.
  useEffect(() => {
    if (!useLocal || !models || !models.length) return;
    if (!models.includes(String(local.model_name || ""))) void save({ local: { model_name: models[0] } });
  }, [models, useLocal]); // eslint-disable-line react-hooks/exhaustive-deps

  const detect = async () => {
    setDetecting(true);
    try {
      const resp = await fetch(`${apiBase}/provider/detect`);
      const data = resp.ok ? await resp.json() : { providers: [] };
      setDetected(Array.isArray(data.providers) ? data.providers : []);
    } catch {
      setDetected([]);
    } finally {
      setDetecting(false);
    }
  };

  // Look for running apps whenever this page opens on local models.
  useEffect(() => {
    if (useLocal) void detect();
  }, [useLocal]); // eslint-disable-line react-hooks/exhaustive-deps

  const running = (detected || []).filter((row) => row.running);

  // The chosen app isn't answering but another one is running: use that one.
  useEffect(() => {
    if (userPicked.current || !useLocal || !detected || models === null || models.length) return;
    const other = detected.find((row) => row.running && row.provider !== provider);
    if (other) void useDetected(other);
  }, [detected, models, useLocal, provider]); // eslint-disable-line react-hooks/exhaustive-deps
  const useDetected = (row: DetectRow) =>
    save({ use_local_models: true, local: { provider: row.provider, base_url: row.base_url, model_name: row.models[0] || "" } });

  const pickApp = (value: string) => {
    userPicked.current = true;
    // Switching apps moves to that app's address, unless a custom address was typed.
    const current = String(local.base_url || "").trim();
    const isStock = !current || Object.values(LOCAL_DEFAULT_URLS).some((u) => current.replace(/\/v1\/?$/, "").replace("127.0.0.1", "localhost") === u);
    const row = running.find((r) => r.provider === value);
    void save({ local: { provider: value, ...(isStock ? { base_url: LOCAL_DEFAULT_URLS[value] || current } : {}), model_name: row?.models[0] || "" } });
  };

  const runTest = async (check: "catalog" | "generation" = "catalog") => {
    setTest({ busy: true });
    try {
      const target = useLocal ? (provider === "ollama" ? "ollama" : "local") : provider;
      const resp = await fetch(`${apiBase}/settings/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target, check, model: useLocal ? undefined : s[provider]?.model, base_url: useLocal ? local.base_url : undefined }),
      });
      const data = await resp.json();
      setTest({ busy: false, ok: Boolean(data.ok), message: String(data.message || "") });
    } catch (err) {
      setTest({ busy: false, ok: false, message: String(err) });
    }
  };

  return (
    <>
      <Group>
        <Row label="Where models run">
          <Segmented
            value={useLocal ? "local" : "cloud"}
            onChange={(v) => save({ use_local_models: v === "local" })}
            options={[
              { value: "local", label: "On this PC" },
              { value: "cloud", label: "Cloud" },
            ]}
          />
        </Row>
      </Group>
      {useLocal ? (
        <Group title="Local model" description="The default for chats. You can still switch models per chat from the composer.">
          <Row
            label="Running on this PC"
            help={
              detecting
                ? "Looking for LM Studio, Ollama and others…"
                : detected === null
                  ? "Checks the usual ports for local model apps."
                  : running.length
                    ? running.map((r) => `${LOCAL_PROVIDERS.find((p) => p.value === r.provider)?.label || r.provider} (${r.models.length} model${r.models.length === 1 ? "" : "s"})`).join(" · ")
                    : <span className="st-err">Nothing found. Open LM Studio, load a model and turn on its local server (Developer tab), then check again.</span>
            }
          >
            <div style={{ display: "flex", gap: 6 }}>
              {running.length && running[0].provider !== provider ? (
                <button type="button" className="es-btn es-btn-sm" onClick={() => void useDetected(running[0])}>
                  Use {LOCAL_PROVIDERS.find((p) => p.value === running[0].provider)?.label || running[0].provider}
                </button>
              ) : null}
              <button type="button" className="es-btn es-btn-sm" disabled={detecting} onClick={() => { void detect(); setReloadKey((k) => k + 1); }}>
                {detecting ? "Checking…" : "Check again"}
              </button>
            </div>
          </Row>
          <Row label="App">
            <Select value={provider} options={LOCAL_PROVIDERS} onChange={pickApp} />
          </Row>
          <Row label="Server address" help={`Where ${providerLabel}'s server listens. Usually ${LOCAL_DEFAULT_URLS[provider] || "set by the app"}.`}>
            <TextField mono value={local.base_url || ""} placeholder={LOCAL_DEFAULT_URLS[provider] || "http://localhost:1234"} onCommit={(v) => save({ local: { base_url: v } })} />
          </Row>
          <Row
            label="Default model"
            help={
              models === null
                ? "Loading models…"
                : models.length
                  ? undefined
                  : <span className="st-err">{providerLabel} isn't answering at {local.base_url || LOCAL_DEFAULT_URLS[provider]}. Start its server and load a model.</span>
            }
          >
            <Select
              wide
              value={String(local.model_name || "")}
              options={(models || []).map((m) => ({ value: m, label: m }))}
              onChange={(v) => save({ local: { model_name: v } })}
            />
          </Row>
          <Row label="Context length" help="Match what you set in LM Studio. Agents trim old messages to stay inside it.">
            <Select
              value={String(local.context_length || 32768)}
              onChange={(v) => save({ local: { context_length: Number(v) } })}
              options={[8192, 16384, 32768, 64358, 65536, 131072].map((n) => ({ value: String(n), label: `${Math.round(n / 1024)}k tokens` }))}
            />
          </Row>
          <Row label="Creativity" help="Temperature. Lower is more focused, higher is more varied.">
            <Select
              value={String(local.temperature ?? 0.7)}
              onChange={(v) => save({ local: { temperature: Number(v) } })}
              options={["0.2", "0.4", "0.6", "0.7", "0.8", "1.0"].map((t) => ({ value: t, label: t }))}
            />
          </Row>
          <Row label="Check connection" help={test.message ? <span className={test.ok ? "st-ok" : "st-err"}>{test.message}</span> : "Make sure EchoSpeak can reach the model server."}>
            <button type="button" className="es-btn es-btn-sm" disabled={test.busy} onClick={() => void runTest()}>
              {test.busy ? "Testing…" : "Test"}
            </button>
          </Row>
        </Group>
      ) : (
        <Group title="Cloud model">
          <Row label="Provider">
            <Select value={provider} onChange={(v) => save({ default_cloud_provider: v })} options={[{ value: "openai", label: "OpenAI / ChatGPT" }, { value: "gemini", label: "Google Gemini" }, { value: "anthropic", label: "Anthropic / Claude" }, { value: "xai", label: "xAI / Grok" }]} />
          </Row>
          <Row label="API key" stack help="Use this provider's developer API key. Save it to load the models your account can access.">
            <SecretField isSet={Boolean(s[provider]?.api_key)} onCommit={(v) => save({ [provider]: { api_key: v } })} />
          </Row>
          <Row label="Available models" help={models === null ? "Loading the provider's model catalog…" : catalogMessage}>
            <div style={{ display: "flex", gap: 6, width: "100%" }}>
              <div className="st-select is-wide"><select aria-label="Available cloud models" value={String(s[provider]?.model || "")} onChange={(e) => void save({ [provider]: { model: e.target.value } })}>
                <option value="" disabled>Choose a model</option>
                {s[provider]?.model && !catalog.some(m => m.id === s[provider].model) ? <option value={s[provider].model}>{s[provider].model} (custom ID)</option> : null}
                {(catalog.length ? catalog : (models || []).map(id => ({ id, name: id, chat: true, live: false, reason: "" }))).map(m => <option key={m.id} value={m.id} disabled={!m.chat}>{m.id}{m.live ? " · Live audio" : ""}{!m.chat ? " · specialized API" : ""}</option>)}
              </select></div>
              <button type="button" className="es-btn es-btn-sm" onClick={() => setReloadKey((k) => k + 1)}>Refresh</button>
            </div>
          </Row>
          {provider === "anthropic" && <Row label="Workspace ID" help="Optional. Required for Claude personal/service keys that can access multiple workspaces."><TextField mono value={s.anthropic?.workspace_id || ""} onCommit={(v) => save({ anthropic: { workspace_id: v } })} /></Row>}
          <Row label="Model ID" help="The exact API ID. Custom IDs stay selected even when absent from the catalog.">
            <TextField mono wide value={s[provider]?.model || ""} onCommit={(v) => save({ [provider]: { model: v } })} />
          </Row>
          {provider === "gemini" && /live|native-audio/i.test(String(s.gemini?.model || "")) && <p className="st-muted">Gemini Live returns speech and its transcription. Use Read or Voice to hear replies, and enable Live mic in the chat toolbar to stream your microphone to Google. Leave Live mic off for local transcription. API audio charges and account model access apply.</p>}
          <Row label="Check provider" help="Catalog access and working chat are separate checks. Test response checks a reply and a harmless tool round-trip; it may incur API charges.">
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button type="button" className="es-btn es-btn-sm" disabled={test.busy} onClick={() => void runTest("catalog")}>Check catalog</button>
              <button type="button" className="es-btn es-btn-sm" disabled={test.busy || !s[provider]?.model} onClick={() => void runTest("generation")}>{test.busy ? "Checking…" : "Test response"}</button>
            </div>
          </Row>
          {test.message && <p className={test.ok ? "st-ok" : "st-err"} role="status">{test.message}</p>}
        </Group>
      )}
    </>
  );
}

// ── Agents ──────────────────────────────────────────────────────────────
function AgentsSection({ agents, onEdit }: { agents: LeanPersona[]; onEdit(agent: LeanPersona | null): void }) {
  return (
    <Group
      title="Your agents"
      description="Each agent has its own personality and tools. Chat with one directly, or put several in a group chat."
      action={<button type="button" className="es-btn es-btn-sm es-btn-primary" onClick={() => onEdit(null)}>New agent</button>}
    >
      {agents.map((agent) => (
        <Row
          key={agent.id}
          label={
            <span className="st-agent-label">
              <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={28} />
              <span>
                <strong>{agent.name}</strong>
                <small>{agent.title || "Agent"}{agent.builtin ? " · built in" : ""}</small>
              </span>
            </span>
          }
          help={agent.description}
        >
          <button type="button" className="es-btn es-btn-sm" onClick={() => onEdit(agent)}>Edit</button>
        </Row>
      ))}
    </Group>
  );
}

// ── Personality ─────────────────────────────────────────────────────────
function PersonalitySection({ apiBase }: { apiBase: string }) {
  const [soul, setSoul] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [state, setState] = useState("");
  useEffect(() => {
    fetch(`${apiBase}/soul`)
      .then((r) => r.json())
      .then((d) => {
        setSoul(String(d.content || ""));
        setDraft(String(d.content || ""));
      })
      .catch(() => setSoul(""));
  }, [apiBase]);
  const dirty = soul !== null && draft !== soul;
  const saveSoul = async () => {
    setState("Saving…");
    try {
      const resp = await fetch(`${apiBase}/soul`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: draft }) });
      if (!resp.ok) throw new Error(await resp.text());
      setSoul(draft);
      setState("Saved");
    } catch (err) {
      setState(`Couldn't save: ${err}`);
    }
  };
  return (
    <Group
      title="Echo's soul"
      description="SOUL.md defines Echo's identity, voice, and boundaries. Other agents have their own personality under Agents."
      action={
        <div className="st-inline-actions">
          <span className="st-muted">{state}</span>
          <button type="button" className="es-btn es-btn-sm" disabled={!dirty} onClick={() => setDraft(soul || "")}>Revert</button>
          <button type="button" className="es-btn es-btn-sm es-btn-primary" disabled={!dirty} onClick={() => void saveSoul()}>Save</button>
        </div>
      }
    >
      <textarea className="st-textarea" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} rows={22} placeholder={soul === null ? "Loading…" : "# Echo"} />
    </Group>
  );
}

// ── Permissions ─────────────────────────────────────────────────────────
const PERMS: { key: string; label: string; help: string }[] = [
  { key: "allow_file_write", label: "Create and edit files", help: "Inside the folders below. Every overwrite is checkpointed so it can be undone." },
  { key: "allow_terminal_commands", label: "Run terminal commands", help: "Uses the Terminal settings. Dangerous commands still ask first." },
  { key: "allow_playwright", label: "Browse the web in a real browser", help: "For pages that need clicking or logging in." },
  { key: "allow_open_application", label: "Open apps", help: "Launch programs on this PC." },
  { key: "allow_open_chrome", label: "Open Chrome", help: "Open links in your browser." },
  { key: "allow_desktop_automation", label: "Control the desktop", help: "Click, type, and switch windows. Always asks first." },
  { key: "allow_email", label: "Email", help: "Read and send email with the account in Channels." },
  { key: "allow_self_modification", label: "Edit EchoSpeak itself", help: "Let agents change EchoSpeak's own code. For development only." },
];

function PermissionsSection({ s, save }: { s: SettingsMap; save: Save }) {
  const master = asBool(s.enable_system_actions);
  return (
    <>
      <Group>
        <Row label="Allow actions on this computer" help="Master switch. When off, agents can still chat, search the web, and read files.">
          <Toggle checked={master} onChange={(v) => save({ enable_system_actions: v })} label="Allow actions" />
        </Row>
      </Group>
      <Group title="What agents can do">
        {PERMS.map((p) => (
          <Row key={p.key} label={p.label} help={p.help} disabled={!master}>
            <Toggle checked={asBool(s[p.key])} disabled={!master} onChange={(v) => save({ [p.key]: v })} label={p.label} />
          </Row>
        ))}
      </Group>
      <Group title="Folders agents can use" description="Agents read and write only inside these. A project folder attached to a chat is added automatically.">
        <Row label="Main workspace" help="Relative paths and new projects go here." stack>
          <TextField wide mono value={s.file_tool_root || ""} placeholder="C:\\Users\\you\\Desktop\\Projects" onCommit={(v) => save({ file_tool_root: v })} />
        </Row>
        <Row label="Also allow" stack>
          <ListEditor mono items={asList(s.file_tool_extra_roots)} placeholder="C:\\Users\\you\\Documents\\code" onChange={(items) => save({ file_tool_extra_roots: items })} />
        </Row>
      </Group>
    </>
  );
}

// ── Terminal ────────────────────────────────────────────────────────────
type TerminalInfo = {
  mode: string;
  network: string;
  host_shell: string;
  mounts: { host: string; container: string }[];
  docker?: { docker_installed?: boolean; docker_running?: boolean; container_running?: boolean; image_ready?: boolean; detail?: string };
  processes: { id: string; command: string }[];
  setup: { running: boolean; message: string; ok: boolean | null };
};

export function TerminalSection({ s, save, apiBase }: { s: SettingsMap; save: Save; apiBase: string }) {
  const [info, setInfo] = useState<TerminalInfo | null>(null);
  const refresh = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/lean/terminal`);
      if (resp.ok) setInfo(await resp.json());
    } catch {
      // keep last known
    }
  }, [apiBase]);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), info?.setup?.running ? 2000 : 8000);
    return () => window.clearInterval(timer);
  }, [refresh, info?.setup?.running]);

  const rawMode = String(s.terminal_execution_mode || "auto");
  const mode = rawMode === "host" ? "host" : rawMode === "docker" ? "docker" : "auto";
  const docker = info?.docker || {};
  // In Auto, the sandbox is used whenever Docker is running.
  const usesSandbox = mode === "docker" || (mode === "auto" && Boolean(docker.docker_running));
  const network = ["none", "off"].includes(String(s.terminal_docker_network)) ? "off" : ["bridge", "on"].includes(String(s.terminal_docker_network)) ? "on" : "ask";
  const setup = async () => {
    await fetch(`${apiBase}/lean/terminal/setup`, { method: "POST" });
    void refresh();
  };
  const reset = async () => {
    if (!window.confirm("Reset the sandbox? Installed packages inside it are removed. Your files are not touched.")) return;
    await fetch(`${apiBase}/lean/terminal/reset`, { method: "POST" });
    void refresh();
  };

  let tone: "ok" | "warn" | "err" | "idle" = "idle";
  let statusText = "Checking…";
  if (info) {
    if (mode === "host") {
      tone = "ok";
      statusText = `Ready · ${info.host_shell}`;
    } else if (mode === "auto" && !docker.docker_running) {
      tone = "ok";
      statusText = `Running on this PC · start Docker Desktop to use the sandbox`;
    } else if (info.setup?.running) {
      tone = "warn";
      statusText = info.setup.message || "Setting up…";
    } else if (!docker.docker_installed) {
      tone = "err";
      statusText = "Docker isn't installed";
    } else if (!docker.docker_running) {
      tone = "warn";
      statusText = "Docker Desktop isn't running";
    } else if (docker.container_running) {
      tone = "ok";
      statusText = "Sandbox running";
    } else {
      tone = "warn";
      statusText = docker.image_ready ? "Sandbox stopped (starts on first command)" : "Sandbox not set up yet";
    }
  }

  return (
    <>
      <Group title="Where commands run">
        <ChoiceCards
          value={mode}
          onChange={(v) => save({ terminal_execution_mode: v })}
          options={[
            {
              value: "auto",
              title: "Auto",
              badge: "Recommended",
              body: "Uses the sandbox whenever Docker Desktop is running, and this PC otherwise. Agents ask before leaving the sandbox.",
            },
            {
              value: "docker",
              title: "Sandbox",
              badge: "Safer",
              body: "A Linux container with Node, Python, and git. It only sees the folders agents are allowed to use, so commands can't touch the rest of your PC.",
            },
            {
              value: "host",
              title: "This PC",
              body: "Runs PowerShell directly, with every tool you have installed. Fastest and most capable. Risky commands still ask first.",
            },
          ]}
        />
      </Group>
      <Group title="Status">
        <Row label={<Status tone={tone}>{statusText}</Status>} help={usesSandbox ? info?.setup?.ok === false ? info.setup.message : docker.detail : undefined}>
          {usesSandbox ? (
            <div className="st-inline-actions">
              <button type="button" className="es-btn es-btn-sm" onClick={() => void reset()} disabled={!docker.docker_running}>Reset</button>
              <button type="button" className="es-btn es-btn-sm es-btn-primary" onClick={() => void setup()} disabled={Boolean(info?.setup?.running) || !docker.docker_installed}>
                {docker.container_running ? "Restart" : "Set up"}
              </button>
            </div>
          ) : null}
        </Row>
        {usesSandbox && info?.mounts?.length ? (
          <Row label="Folders inside the sandbox" stack>
            <div className="st-mounts">
              {info.mounts.map((m) => (
                <div key={m.host} className="st-mount"><span className="is-mono">{m.host}</span><span className="st-muted">→</span><span className="is-mono">{m.container}</span></div>
              ))}
            </div>
          </Row>
        ) : null}
        {info?.processes?.length ? (
          <Row label="Background processes" stack>
            <div className="st-mounts">{info.processes.map((p) => <div key={p.id} className="st-mount is-mono">{p.id} · {p.command}</div>)}</div>
          </Row>
        ) : null}
      </Group>
      {mode !== "host" ? (
        <Group title="Sandbox options">
          <Row label="Internet access" help="Needed for npm install, pip install, and git clone. Ask first keeps the sandbox offline until a command needs it.">
            <Select
              value={network}
              onChange={(v) => save({ terminal_docker_network: v })}
              options={[
                { value: "ask", label: "Ask first" },
                { value: "on", label: "Always on" },
                { value: "off", label: "Off" },
              ]}
            />
          </Row>
          <Row label="Memory limit">
            <Select value={String(s.terminal_docker_memory || "4g").replace("512m", "4g")} onChange={(v) => save({ terminal_docker_memory: v })} options={["2g", "4g", "6g", "8g"].map((m) => ({ value: m, label: m.toUpperCase().replace("G", " GB") }))} />
          </Row>
        </Group>
      ) : null}
    </>
  );
}

// ── Search ──────────────────────────────────────────────────────────────
export function SearchSection({ s, save }: { s: SettingsMap; save: Save }) {
  const provider = String(s.web_search_provider || "auto");
  return (
    <>
      <Group>
        <Row label="Search engine" help="Auto tries the best one that's configured, then falls back to DuckDuckGo.">
          <Select
            value={provider}
            onChange={(v) => save({ web_search_provider: v })}
            options={[
              { value: "auto", label: "Automatic" },
              { value: "duckduckgo", label: "DuckDuckGo" },
              { value: "searxng", label: "SearXNG (self-hosted)" },
              { value: "brave", label: "Brave Search" },
              { value: "tavily", label: "Tavily" },
            ]}
          />
        </Row>
        {provider === "searxng" || s.searxng_base_url ? (
          <Row label="SearXNG address"><TextField mono value={s.searxng_base_url || ""} placeholder="http://localhost:8080" onCommit={(v) => save({ searxng_base_url: v })} /></Row>
        ) : null}
        <Row label="Brave API key"><SecretField isSet={Boolean(s.brave_search_api_key)} onCommit={v => save({ brave_search_api_key: v })} /></Row>
        <Row label="Tavily API key"><SecretField isSet={Boolean(s.tavily_api_key)} onCommit={v => save({ tavily_api_key: v })} /></Row>
        <Row label="Results per search">
          <Select value={String(s.web_search_max_results || 8)} onChange={(v) => save({ web_search_max_results: Number(v) })} options={[5, 8, 10, 15, 20].map((n) => ({ value: String(n), label: String(n) }))} />
        </Row>
      </Group>
      <Group title="Blocked sites" description="Results from these domains are dropped.">
        <ListEditor items={asList(s.web_search_blocked_domains)} placeholder="example.com" onChange={(items) => save({ web_search_blocked_domains: items })} />
      </Group>
    </>
  );
}

// ── Memory ──────────────────────────────────────────────────────────────
function MemorySection({ s, save, openAdvanced }: { s: SettingsMap; save: Save; openAdvanced(page: AdvancedPage): void }) {
  return (
    <>
      <Group description="Agents save lasting facts with their memory tool and recall them automatically.">
        <Row label="Remember conversations" help="Keep a searchable record of past chats for recall.">
          <Toggle checked={asBool(s.session_memory_enabled)} onChange={(v) => save({ session_memory_enabled: v })} label="Remember conversations" />
        </Row>
        <Row label="Use your documents" help="Search uploaded PDFs and notes when answering.">
          <Toggle checked={asBool(s.document_rag_enabled)} onChange={(v) => save({ document_rag_enabled: v })} label="Use documents" />
        </Row>
      </Group>
      <Group title="Manage">
        <Row label="Saved memories" help="Review, edit, or delete what agents remember.">
          <button type="button" className="es-btn es-btn-sm" onClick={() => openAdvanced("memory")}>Open</button>
        </Row>
        <Row label="Documents" help="Upload and remove documents.">
          <button type="button" className="es-btn es-btn-sm" onClick={() => openAdvanced("memory")}>Open</button>
        </Row>
      </Group>
    </>
  );
}

// ── Automations ─────────────────────────────────────────────────────────
type RoutineItem = {
  id: string;
  name: string;
  prompt: string;
  enabled: boolean;
  trigger_type: string;
  schedule: string | null;
  agent_id: string;
  delivery_channels: string[];
  last_run: string | null;
  next_run: string | null;
  last_result_status: string;
  last_error: string;
  session_id: string;
};

const SCHEDULES = [
  { value: "", label: "Only when I run it" },
  { value: "0 * * * *", label: "Every hour" },
  { value: "0 8 * * *", label: "Every day at 8:00 AM" },
  { value: "0 9 * * 1-5", label: "Weekdays at 9:00 AM" },
  { value: "0 18 * * *", label: "Every day at 6:00 PM" },
  { value: "0 9 * * 1", label: "Mondays at 9:00 AM" },
  { value: "custom", label: "Custom (cron)…" },
];

function describeSchedule(r: RoutineItem): string {
  if (r.trigger_type !== "schedule" || !r.schedule) return "Manual";
  return SCHEDULES.find((s) => s.value === r.schedule)?.label || `Cron: ${r.schedule}`;
}

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/** Routines list + editor. Shared by Settings › Automations and the Routines page. */
export function RoutinesGroup({ apiBase, agents, embedded = false }: { apiBase: string; agents: LeanPersona[]; embedded?: boolean }) {
  const [items, setItems] = useState<RoutineItem[]>([]);
  const [editing, setEditing] = useState<Partial<RoutineItem> | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}/lean/routines`);
      if (resp.ok) setItems((await resp.json()).items || []);
    } catch {
      // keep list
    }
  }, [apiBase]);
  useEffect(() => {
    void load();
  }, [load]);

  const call = async (path: string, method: string, body?: any) => {
    setError("");
    const resp = await fetch(`${apiBase}/lean/routines${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        detail = (await resp.json()).detail || detail;
      } catch {
        // keep status
      }
      setError(detail);
      return false;
    }
    await load();
    return true;
  };

  return (
    <>
      <Group
        title={embedded ? "Your routines" : "Routines"}
        description={embedded ? undefined : "Tasks your agents run on a schedule or when you tap Run. Results show up in their own chat."}
        action={<button type="button" className="es-btn es-btn-sm es-btn-primary" onClick={() => setEditing({ name: "", prompt: "", schedule: "0 8 * * *", agent_id: "echo", delivery_channels: [] })}>New routine</button>}
      >
        {items.length === 0 && !editing ? <div className="st-empty-row">No routines yet. Try “Every morning, summarize today's weather and top tech news.”</div> : null}
        {items.map((r) => {
          const agent = agents.find((a) => a.id === r.agent_id);
          return (
            <Row
              key={r.id}
              label={<span className="st-routine-title">{r.name}<Status tone={r.last_result_status === "failed" ? "err" : r.last_result_status ? "ok" : "idle"}>{r.last_result_status === "failed" ? "Last run failed" : r.last_run ? `Ran ${when(r.last_run)}` : "Not run yet"}</Status></span>}
              help={<>{describeSchedule(r)} · {agent?.name || "Echo"}{r.next_run && r.enabled && r.trigger_type === "schedule" ? ` · next ${when(r.next_run)}` : ""}{r.last_result_status === "failed" && r.last_error ? <span className="st-err"> · {r.last_error.slice(0, 120)}</span> : null}</>}
            >
              <div className="st-inline-actions">
                <Toggle checked={r.enabled} onChange={(v) => void call(`/${r.id}`, "PATCH", { enabled: v })} label={`Enable ${r.name}`} />
                <button type="button" className="es-btn es-btn-sm" onClick={() => void call(`/${r.id}/run`, "POST")}>Run</button>
                <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => setEditing({ ...r })}>Edit</button>
              </div>
            </Row>
          );
        })}
        {editing ? (
          <RoutineEditor
            initial={editing}
            agents={agents}
            error={error}
            onCancel={() => {
              setEditing(null);
              setError("");
            }}
            onDelete={editing.id ? async () => {
              if (window.confirm(`Delete “${editing.name}”?`) && (await call(`/${editing.id}`, "DELETE"))) setEditing(null);
            } : undefined}
            onSave={async (data) => {
              const ok = editing.id ? await call(`/${editing.id}`, "PATCH", data) : await call("", "POST", data);
              if (ok) setEditing(null);
            }}
          />
        ) : null}
      </Group>
    </>
  );
}

function AutomationsSection({ s, save, apiBase, agents }: { s: SettingsMap; save: Save; apiBase: string; agents: LeanPersona[] }) {
  return (
    <>
      <RoutinesGroup apiBase={apiBase} agents={agents} />
      <Group title="Heartbeat" description="A periodic check-in where Echo looks at your todos, projects, and activity and only speaks up if something matters.">
        <Row label="Enable heartbeat">
          <Toggle checked={asBool(s.heartbeat_enabled)} onChange={(v) => save({ heartbeat_enabled: v })} label="Enable heartbeat" />
        </Row>
        <Row label="Check every" disabled={!asBool(s.heartbeat_enabled)}>
          <Select value={String(s.heartbeat_interval || 30)} onChange={(v) => save({ heartbeat_interval: Number(v) })} options={[15, 30, 60, 120, 240].map((n) => ({ value: String(n), label: n < 60 ? `${n} minutes` : `${n / 60} hour${n > 60 ? "s" : ""}` }))} />
        </Row>
        <Row label="Also send to" disabled={!asBool(s.heartbeat_enabled)}>
          <ChannelPicks value={asList(s.heartbeat_channels).filter((c) => c !== "web")} onChange={(v) => save({ heartbeat_channels: ["web", ...v] })} />
        </Row>
      </Group>
    </>
  );
}

function ChannelPicks({ value, onChange }: { value: string[]; onChange(v: string[]): void }) {
  return (
    <div className="st-picks">
      {["discord", "telegram"].map((c) => {
        const on = value.includes(c);
        return (
          <button key={c} type="button" className={`st-pick${on ? " is-on" : ""}`} aria-pressed={on} onClick={() => onChange(on ? value.filter((x) => x !== c) : [...value, c])}>
            {c === "discord" ? "Discord" : "Telegram"}
          </button>
        );
      })}
    </div>
  );
}

function RoutineEditor({
  initial,
  agents,
  error,
  onSave,
  onCancel,
  onDelete,
}: {
  initial: Partial<RoutineItem>;
  agents: LeanPersona[];
  error: string;
  onSave(data: any): Promise<void>;
  onCancel(): void;
  onDelete?(): Promise<void>;
}) {
  const presetValue = (initial.trigger_type === "schedule" || !initial.id) && initial.schedule ? (SCHEDULES.some((s) => s.value === initial.schedule) ? String(initial.schedule) : "custom") : "";
  const [name, setName] = useState(initial.name || "");
  const [prompt, setPrompt] = useState(initial.prompt || "");
  const [preset, setPreset] = useState(presetValue);
  const [cron, setCron] = useState(initial.schedule || "0 8 * * *");
  const [agentId, setAgentId] = useState(initial.agent_id || "echo");
  const [channels, setChannels] = useState<string[]>((initial.delivery_channels || []).filter((c) => c !== "web"));
  const schedule = preset === "custom" ? cron : preset;
  return (
    <div className="st-editor">
      <div className="st-editor-grid">
        <label className="es-field"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Morning briefing" autoFocus /></label>
        <label className="es-field">
          <span>Agent</span>
          <Select wide value={agentId} onChange={setAgentId} options={agents.map((a) => ({ value: a.id, label: a.name }))} />
        </label>
      </div>
      <label className="es-field"><span>What should it do?</span><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} placeholder="Check today's weather in Denver and the top 3 tech headlines, then give me a short briefing." /></label>
      <div className="st-editor-grid">
        <label className="es-field">
          <span>When</span>
          <Select wide value={preset} onChange={setPreset} options={SCHEDULES} />
        </label>
        {preset === "custom" ? (
          <label className="es-field"><span>Cron (local time)</span><input className="is-mono" value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 8 * * *" /></label>
        ) : (
          <div className="es-field"><span>Also send to</span><ChannelPicks value={channels} onChange={setChannels} /></div>
        )}
      </div>
      {preset === "custom" ? <div className="es-field"><span>Also send to</span><ChannelPicks value={channels} onChange={setChannels} /></div> : null}
      {error ? <div className="es-form-error">{error}</div> : null}
      <div className="st-editor-foot">
        {onDelete ? <button type="button" className="es-btn es-btn-sm es-btn-danger" onClick={() => void onDelete()}>Delete</button> : <span />}
        <div className="st-inline-actions">
          <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={onCancel}>Cancel</button>
          <button
            type="button"
            className="es-btn es-btn-sm es-btn-primary"
            disabled={!name.trim() || !prompt.trim()}
            onClick={() => void onSave({ name: name.trim(), prompt: prompt.trim(), schedule: schedule || null, trigger_type: schedule ? "schedule" : "manual", agent_id: agentId, channels })}
          >
            Save routine
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────
function ChannelsSection({ s, save }: { s: SettingsMap; save: Save }) {
  return (
    <>
      <Group title="Discord" description="Chat with your agents from Discord. Routines and the heartbeat can DM you here.">
        <Row label="Enable Discord bot">
          <Toggle checked={asBool(s.allow_discord_bot)} onChange={(v) => save({ allow_discord_bot: v })} label="Enable Discord" />
        </Row>
        <Row label="Bot token" stack><SecretField isSet={Boolean(s.discord_bot_token)} onCommit={(v) => save({ discord_bot_token: v })} /></Row>
        <Row label="Your Discord user ID" help="Owner of the bot. Results are DMed to this account.">
          <TextField mono value={s.discord_bot_owner_id || ""} placeholder="123456789012345678" onCommit={(v) => save({ discord_bot_owner_id: v })} />
        </Row>
      </Group>
      <Group title="Telegram" description="Message your agents from Telegram.">
        <Row label="Enable Telegram bot">
          <Toggle checked={asBool(s.allow_telegram_bot)} onChange={(v) => save({ allow_telegram_bot: v })} label="Enable Telegram" />
        </Row>
        <Row label="Bot token" stack><SecretField isSet={Boolean(s.telegram_bot_token)} onCommit={(v) => save({ telegram_bot_token: v })} /></Row>
        <Row label="Allowed user IDs" stack>
          <ListEditor mono items={asList(s.telegram_allowed_users)} placeholder="Telegram user ID" onChange={(items) => save({ telegram_allowed_users: items })} />
        </Row>
      </Group>
      <Group title="Email">
        <Row label="Address"><TextField value={s.email_username || ""} placeholder="you@example.com" onCommit={(v) => save({ email_username: v })} /></Row>
        <Row label="App password" stack><SecretField isSet={Boolean(s.email_password)} onCommit={(v) => save({ email_password: v })} /></Row>
        <Row label="Mail servers" help="IMAP / SMTP host">
          <div className="st-inline-actions">
            <TextField mono value={s.email_imap_host || ""} placeholder="imap.gmail.com" onCommit={(v) => save({ email_imap_host: v })} />
            <TextField mono value={s.email_smtp_host || ""} placeholder="smtp.gmail.com" onCommit={(v) => save({ email_smtp_host: v })} />
          </div>
        </Row>
      </Group>
    </>
  );
}

// ── About ───────────────────────────────────────────────────────────────
function UpdateRow() {
  const [info, setInfo] = useState<DesktopUpdateInfo | null>(null);
  const [busy, setBusy] = useState<"" | "checking" | "installing">("");
  const [progress, setProgress] = useState<DesktopUpdateProgress | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop: (() => void) | undefined;
    void onDesktopUpdateProgress(setProgress).then((unlisten) => (stop = unlisten));
    return () => stop?.();
  }, []);

  const check = async () => {
    setBusy("checking");
    setError("");
    try {
      const result = await checkForDesktopUpdate();
      setInfo(result); announceUpdate(result);
    } catch (err) {
      setError(String((err as Error)?.message || err));
    } finally {
      setBusy("");
    }
  };
  useEffect(() => {
    void check();
  }, []);

  const install = async () => {
    setBusy("installing");
    setError("");
    try {
      await installDesktopUpdate(); // EchoSpeak closes and restarts on success.
    } catch (err) {
      setError(String((err as Error)?.message || err));
      setBusy("");
    }
  };

  let help = "Checking…";
  let tone: "ok" | "warn" | "err" | "idle" = "idle";
  if (error) {
    help = error;
    tone = "err";
  } else if (busy === "installing") {
    const pct = progress?.total ? Math.round((progress.downloaded / progress.total) * 100) : null;
    help = progress?.phase === "installing" ? "Installing… EchoSpeak will restart." : `Downloading${pct === null ? "…" : ` · ${pct}%`}`;
    tone = "warn";
  } else if (info && !info.configured) {
    help = "This build was made without update signing, so it can't update itself.";
  } else if (info?.available) {
    const headline = info.notes.trim().split(/\r?\n/)[0] || "";
    help = `Version ${info.version} is ready to install.${headline ? ` ${headline}` : ""}`;
    tone = "warn";
  } else if (info) {
    help = "You have the latest version.";
    tone = "ok";
  }

  return (
    <Row label={<Status tone={tone}>Updates</Status>} help={help}>
      {info?.available ? (
        <button type="button" className="es-btn es-btn-sm es-btn-primary" disabled={Boolean(busy)} onClick={() => void install()}>
          {busy === "installing" ? "Updating…" : `Update to ${info.version}`}
        </button>
      ) : (
        <button type="button" className="es-btn es-btn-sm" disabled={Boolean(busy) || info?.configured === false} onClick={() => void check()}>
          {busy === "checking" ? "Checking…" : "Check for updates"}
        </button>
      )}
    </Row>
  );
}

function AboutSection({ apiBase, openAdvanced, reload }: { apiBase: string; openAdvanced(page: AdvancedPage): void; reload(): Promise<void> }) {
  const [status, setStatus] = useState<any>(null);
  useEffect(() => {
    fetch(`${apiBase}/lean/status`).then((r) => r.json()).then(setStatus).catch(() => setStatus(null));
  }, [apiBase]);
  return (
    <>
      <Group>
        <Row label="EchoSpeak" help="Local-first agents. Your data stays on this PC.">
          <span className="st-muted is-mono">{String(import.meta.env.VITE_APP_VERSION || "development")}</span>
        </Row>
        {isDesktopRuntime() ? <UpdateRow /> : null}
        <Row label="Agent runtime" help={status ? `Lean loop · up to ${status.max_iterations} steps · ${Math.round((status.context_tokens || 0) / 1000)}k context` : "…"}>
          <Status tone={status ? "ok" : "idle"}>{status ? "Active" : "…"}</Status>
        </Row>
        <Row label="Reload settings" help="Pick up changes made outside this window.">
          <button type="button" className="es-btn es-btn-sm" onClick={() => void reload()}>Reload</button>
        </Row>
      </Group>
      <Group title="More" description="Less common options are in Advanced.">
        {([
          ["connections", "Connections, skills and MCP servers"],
          ["companion", "Companion avatar"],
          ["settings", "Advanced settings"],
        ] as [AdvancedPage, string][]).map(([page, label]) => (
          <Row key={page} label={label}>
            <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => openAdvanced(page)}>Open</button>
          </Row>
        ))}
      </Group>
    </>
  );
}
