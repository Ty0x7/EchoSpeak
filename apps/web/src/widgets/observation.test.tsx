import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RightPanel } from "./RightPanel";
import { WidgetEnvProvider } from "./env";
import { validateWidget } from "./validate";
import { WidgetView } from "./WidgetView";

const data = { id: "obs-" + "a".repeat(24), session_id: "chat-a", source: "Monitor 1", at: 1700000000, width: 1920, height: 1080, model_width: 1440, model_height: 810 };

describe("screenshots", () => {
  it("validates scoped references, rejecting paths and invalid dimensions", () => {
    expect(validateWidget({ type: "observation", data })).toEqual({ type: "observation", data });
    for (const patch of [{ id: "../../secrets" }, { session_id: "" }, { width: -1 }, { model_height: NaN }, { at: "unknown" }]) {
      expect(validateWidget({ type: "observation", data: { ...data, ...patch } })).toBeNull();
    }
  });
  it("renders a real screenshot card instead of an inaccessible filesystem path", () => {
    const html = renderToStaticMarkup(<WidgetEnvProvider value={{ apiBase: "http://api" }}><WidgetView widget={{ type: "observation", data }} /></WidgetEnvProvider>);
    expect(html.includes("Monitor 1")).toBe(true);
    expect(html.includes("Loading screenshot")).toBe(true);
    expect(html.includes("file://")).toBe(false);
    expect(html.includes("data:image")).toBe(false);
  });
  it("shares one panel with artifacts, activity and research", () => {
    const html = renderToStaticMarkup(<RightPanel apiBase="http://api" sessionId="chat-a" tab="screen" onTab={() => {}} artifact={{ id: "artifact-a" }} observation={data} activity={[]} width={460} onWidth={() => {}} measureRoom={() => ({ room: 1200, scale: 1 })} overlay={false} onClose={() => {}} />);
    expect(html.match(/role="tablist"/g)?.length).toBe(1);
    for (const tab of ["artifact", "activity", "research", "screen"]) expect(html.includes(`data-tab="${tab}"`)).toBe(true);
    expect(html.includes("Screenshot")).toBe(true);
  });
});
