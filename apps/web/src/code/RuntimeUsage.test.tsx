import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RuntimeUsage } from "./RuntimeUsage";

describe("provider subscription observations", () => {
  it("does not present missing metrics as an unused plan", () => {
    const html = renderToStaticMarkup(<RuntimeUsage usage={{ windows: [], unavailable: "Window unavailable" }}/>);
    expect(html.includes("Window unavailable")).toBe(true);
    expect(html.includes("% used")).toBe(false);
    expect(html.includes("<meter")).toBe(false);
  });
  it("shows a reported window, source and provider rejection independently", () => {
    const html = renderToStaticMarkup(<RuntimeUsage usage={{ status: "rejected", observed_at: 100,
      source: "Official runtime event", windows: [{ label: "Weekly", used_percent: 25, resets_at: 200 }] }}/>);
    for (const text of ["25% used", 'value="25"', "Resets", "Official runtime event", "Runtime reported a subscription limit"])
      expect(html.includes(text)).toBe(true);
  });
});
