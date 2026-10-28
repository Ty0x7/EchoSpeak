import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BrandMark } from "../components/BrandMark";
import { API_AGENTS, ConnectedModelBrowser } from "../dashboard/ConnectedModelBrowser";
import { filterConnectors, isOfficial, publisherLine, type Connector } from "./ConnectorGallery";

const connector = (over: Partial<Connector>): Connector => ({
  id: "x", app: "X", field: "things", category: "Work and productivity", publisher: "official (X)", kind: "signin", sign_in: true,
  where: "", needs: [], missing: [], steps: [], watch_out: [], settings: [], homepage: "", server: "", state: "not_connected",
  tool_count: 0, problem: "", ...over,
});

describe("Settings › Connections › Add apps", () => {
  it("finds apps by name, purpose or kind, and lists connected ones first", () => {
    const items = [
      connector({ id: "zapier", app: "Zapier", field: "thousands of other apps" }),
      connector({ id: "todoist", app: "Todoist", field: "tasks and reminders", state: "connected" }),
      connector({ id: "canva", app: "Canva", field: "designs", category: "Creative and media" }),
    ];
    expect(filterConnectors(items, "", "").map(item => item.id)).toEqual(["todoist", "canva", "zapier"]);
    expect(filterConnectors(items, "tasks", "").map(item => item.id)).toEqual(["todoist"]);
    expect(filterConnectors(items, "", "Creative and media").map(item => item.id)).toEqual(["canva"]);
    expect(filterConnectors(items, "creative", "").map(item => item.id)).toEqual(["canva"]);
  });

  it("marks an app's own server as official and anything else as community", () => {
    expect(isOfficial({ publisher: "official (Doist)" })).toBe(true);
    expect(isOfficial({ publisher: "community (someone)" })).toBe(false);
    expect(publisherLine("official (Doist)")).toBe("Made by Doist");
    expect(publisherLine("community (someone/repo)")).toBe("Made by the community (someone/repo)");
    expect(publisherLine("built into EchoSpeak")).toBe("Built into EchoSpeak");
  });

  it("draws logos, keeping dark brands visible and falling back to a letter", () => {
    const todoist = renderToStaticMarkup(<BrandMark id="todoist" name="Todoist" />);
    expect(todoist.includes("<path") && todoist.includes("#E44332")).toBe(true);
    expect(renderToStaticMarkup(<BrandMark id="github" name="GitHub" />).includes('fill="currentColor"')).toBe(true);
    const unknown = renderToStaticMarkup(<BrandMark id="monday" name="monday.com" />);
    expect(unknown.includes("<svg")).toBe(false);
    expect(unknown.includes(">M<")).toBe(true);
  });
});

describe("Chat's API agents", () => {
  it("offers ChatGPT and Claude by API key in Chat, never in Echo Code's local-only picker", () => {
    expect(API_AGENTS.map(agent => agent.name)).toEqual(["ChatGPT", "Claude"]);
    expect(API_AGENTS.map(agent => agent.planHome)).toEqual(["Codex", "Claude Code"]);
    const chat = renderToStaticMarkup(<ConnectedModelBrowser apiBase="http://localhost" info={null} provider="" model="" locked={false} onChoose={() => {}} />);
    expect(chat.includes('aria-label="ChatGPT models"') && chat.includes('aria-label="Claude models"')).toBe(true);
    const code = renderToStaticMarkup(<ConnectedModelBrowser apiBase="http://localhost" info={null} provider="" model="" locked={false} localOnly onChoose={() => {}} />);
    expect(code.includes("ChatGPT models") || code.includes("Claude models")).toBe(false);
  });
});
