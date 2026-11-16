import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { canDropInto, externalTitle } from "../code/CodeTree";
import { contactOrder, directRoom } from "../lean/AgentsHome";
import { AgentAvatar, speakerName } from "../lean/LeanMessage";
import type { LeanPersona, LeanRoom } from "../lean/types";
import { ENVIRONMENT_PAGES, environmentForPage, environmentOf, handoffDraft, loadEnvironment, modelDisplayName, saveEnvironment } from "./environments";

const agent = (id: string, name: string): LeanPersona => ({ id, name, title: "", description: "", soul: "", avatar: "", initials: name[0], model: { provider: "", model_id: "" }, toolsets: [], builtin: id === "echo" });
const room = (over: Partial<LeanRoom>): LeanRoom => ({ id: "r", name: "R", kind: "direct", agent_ids: [], thread_id: "t", created_at: 0, updated_at: 0, last_message_at: 0, last_preview: "", mode: "reply", max_messages: 30, ...over } as LeanRoom);

describe("Chat, Agents and Code", () => {
  it("puts every conversation in one environment, trusting the backend's answer", () => {
    const rooms = new Set(["room-thread"]);
    expect(environmentOf({ id: "a", environment: "agents" }, rooms)).toBe("agents");
    expect(environmentOf({ id: "c", surface: "code" }, rooms)).toBe("code");
    expect(environmentOf({ id: "room-thread" }, rooms)).toBe("agents");
    expect(environmentOf({ id: "plain" }, rooms)).toBe("chat");
    expect(environmentOf({ id: "x", environment: "bogus", surface: "code" }, rooms)).toBe("code");
  });

  it("gives each page one home", () => {
    const pages = [...ENVIRONMENT_PAGES.chat, ...ENVIRONMENT_PAGES.agents, ...ENVIRONMENT_PAGES.code];
    expect(new Set(pages).size).toBe(pages.length);
    expect(environmentForPage("groups")).toBe("agents");
    expect(environmentForPage("artifacts")).toBe("chat");
    expect(environmentForPage("code")).toBe("code");
    // Learning is a harness mechanic, not a page: its diagnostics live in Settings › Advanced.
    expect((pages as string[]).includes("learning")).toBe(false);
  });

  it("remembers the environment, and survives broken storage", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    expect(loadEnvironment(storage)).toBe("chat");
    saveEnvironment(storage, "agents");
    expect(loadEnvironment(storage)).toBe("agents");
    expect(loadEnvironment({ getItem: () => { throw new Error("blocked"); } })).toBe("chat");
    store.set("echospeak.environment", "nonsense");
    expect(loadEnvironment(storage)).toBe("chat");
  });

  it("shows Chat's answerer as the model, never an agent", () => {
    expect(modelDisplayName("gpt-5.5")).toBe("GPT-5.5");
    expect(modelDisplayName("claude-sonnet-5-5")).toBe("Claude Sonnet 5.5");
    expect(modelDisplayName("claude-opus-4-1-20250805")).toBe("Claude Opus 4.1");
    expect(modelDisplayName("qwen3-8b")).toBe("qwen3-8b");
    expect(speakerName({ id: "chat", name: "gpt-5.5" })).toBe("GPT-5.5");
    expect(speakerName({ id: "scout", name: "Jarvis" })).toBe("Jarvis");
    const chat = renderToStaticMarkup(<AgentAvatar id="chat" name="gpt-5.5" provider="openai" />);
    expect(chat.includes("openai.svg") && !chat.includes("lm-face")).toBe(true);
    expect(renderToStaticMarkup(<AgentAvatar id="echo" name="Echo" />).includes("lm-face")).toBe(true);
  });

  it("hands Chat research to an agent as a quoted, bounded draft", () => {
    const draft = handoffDraft("Trip ideas", "Lisbon\nPorto", 100);
    expect(draft).toBe("From my chat “Trip ideas”:\n> Lisbon\n> Porto\n\n");
    expect(handoffDraft("", "x".repeat(50), 10).includes("xxxxxxxxxx…")).toBe(true);
  });
});

describe("one sidebar, three environments", () => {
  const base = {
    collapsed: false, projects: [], sessions: [{ id: "s1", name: "Lisbon trip", at: 0 }], activeProjectId: "", activeSessionId: "", activeView: "chat",
    onToggleCollapsed() {}, onNewSession() {}, onAddFolder() {}, onSelectSession() {}, onRenameSession() {}, onDeleteSession() {},
    onDeleteProject() {}, onView() {}, onSettings() {}, onPage() {}, onEnvironment() {}, onSearchChats: async () => [],
    agents: { count: 1, onNew() {}, list: <span>Jarvis contact</span> },
  };
  const render = (environment: "chat" | "agents" | "code") =>
    renderToStaticMarkup(<ProjectSidebar {...base} environment={environment} codeTree={<span>Code tree</span>} />);

  it("keeps the shell and switcher, and swaps the content", () => {
    const chat = render("chat"), agents = render("agents"), code = render("code");
    for (const html of [chat, agents, code]) {
      expect(html.includes('aria-label="Environment"')).toBe(true);
      expect(html.includes("echo-sidebar")).toBe(true);
      expect(html.includes("Settings")).toBe(true);
    }
    expect(chat.includes('aria-selected="true" title="Talk to the model you choose"')).toBe(true);
    expect(chat.includes("New chat") && chat.includes("Artifacts") && !chat.includes("Routines") && !chat.includes("Jarvis contact")).toBe(true);
    expect(agents.includes("New agent") && agents.includes("Routines") && agents.includes("Jarvis contact") && agents.includes("Conversations")).toBe(true);
    expect(agents.includes("Search messages") && !agents.includes("Artifacts")).toBe(true);
    expect(code.includes("Code tree") && !code.includes("Search chats") && !code.includes("Group chats")).toBe(true);
  });
});

describe("Agents and Code sidebars", () => {
  it("lists Echo first, then whoever messaged most recently", () => {
    const agents = [agent("scout", "Jarvis"), agent("forge", "GLaDOS"), agent("echo", "Echo")];
    const rooms = [room({ agent_ids: ["forge"], thread_id: "f", last_message_at: 20 }), room({ agent_ids: ["scout"], thread_id: "s", last_message_at: 10 }),
      room({ kind: "group", agent_ids: ["scout", "forge"], thread_id: "g", last_message_at: 99 })];
    expect(contactOrder(agents, rooms).map(a => a.id)).toEqual(["echo", "forge", "scout"]);
    expect(directRoom(rooms, "scout")?.thread_id).toBe("s");
    expect(directRoom(rooms, "echo")).toBe(undefined);
  });

  it("moves only into another trusted project", () => {
    expect(canDropInto({ id: "b", trust_state: "trusted" }, "a")).toBe(true);
    expect(canDropInto({ id: "a", trust_state: "trusted" }, "a")).toBe(false);
    expect(canDropInto({ id: "b", trust_state: "untrusted" }, "a")).toBe(false);
    expect(externalTitle({ id: "x", runtime_id: "codex", state: "rolled_back", created_at: 0, project_id: "p" }).startsWith("Codex")).toBe(true);
  });
});
