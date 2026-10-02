import { describe, expect, it } from "vitest";
import { nextStudioTabIndex, STUDIO_SECTION_ORDER } from "./studioNavigation";

describe("Studio keyboard navigation", () => {
  it("wraps arrow navigation without clipping a reachable tab", () => {
    expect(nextStudioTabIndex(13, 14, "ArrowRight")).toEqual(0);
    expect(nextStudioTabIndex(0, 14, "ArrowLeft")).toEqual(13);
  });

  it("supports Home and End", () => {
    expect(nextStudioTabIndex(6, 14, "Home")).toEqual(0);
    expect(nextStudioTabIndex(6, 14, "End")).toEqual(13);
  });

  it("does not consume unrelated keys or invalid tab sets", () => {
    expect(nextStudioTabIndex(2, 14, "Enter")).toEqual(null);
    expect(nextStudioTabIndex(0, 0, "ArrowRight")).toEqual(null);
  });

  it("lists every Studio section in the grouped order", () => {
    expect(STUDIO_SECTION_ORDER).toEqual([
      "settings",
      "search_settings",
      "services",
      "avatar_editor",
      "connections",
      "capabilities",
      "skills",
      "mcp_settings",
      "approvals",
      "memory",
      "docs",
      "soul",
      "overview",
      "projects",
      "executions",
      "automations",
      "system_services",
      "advanced_settings",
    ]);
  });
});
