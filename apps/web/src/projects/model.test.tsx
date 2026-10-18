import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { localReference, SECTIONS } from "./model";
import { ProjectMarkdown } from "./ProjectDocument";
import { RightPanel } from "../widgets/RightPanel";

describe("project report references", () => {
  it("resolves local documents and images within the project", () => {
    expect(localReference("before.png", "reports/roads.md")).toBe("reports/before.png");
    expect(localReference("../src/roads.gd", "reports/roads.md")).toBe("src/roads.gd");
    expect(localReference("../outside.md")).toBeNull();
  });
  for (const value of ["https://example.com", "file:///C:/secret", "//host/share", "C:\\private", "javascript:alert(1)", "%2fprivate", "%00secret", "bad%ZZ"]) {
    it(`does not interpret ${value} as an authorized local file`, () => {
      expect(localReference(value, "reports/roads.md")).toBeNull();
    });
  }
  it("renders project media through the authenticated file bridge without filesystem URLs", () => {
    const html = renderToStaticMarkup(<ProjectMarkdown text={'# Roads\n\n![Before](before.png)\n\n[Source](../src/roads.gd)\n\n<script>alert(1)</script>'} path="reports/roads.md" base="http://api/projects/a/workspace" onFile={() => {}} />);
    expect(html.includes("Loading media")).toBe(true);
    expect(html.includes("Source</button>")).toBe(true);
    expect(html.includes("file://")).toBe(false);
    expect(html.includes("<script>")).toBe(false);
  });
  it("keeps project resources in explicit navigable sections", () => {
    expect(SECTIONS).toEqual(["Reports", "Files", "Conversations", "Research", "Artifacts", "Media", "Routines", "Activity"]);
  });
  it("reuses the right panel without showing an unrelated chat context", () => {
    const html = renderToStaticMarkup(<RightPanel apiBase="http://api" sessionId="unrelated-chat" tab="research" onTab={() => {}} artifact={{ id: "report" }} activity={[]} width={460} onWidth={() => {}} measureRoom={() => ({ room: 1200, scale: 1 })} overlay={false} onClose={() => {}} resourceOnly />);
    expect(html.includes('data-tab="artifact"')).toBe(true);
    expect(html.includes('data-tab="research"')).toBe(false);
    expect(html.includes('data-tab="activity"')).toBe(false);
  });
});
