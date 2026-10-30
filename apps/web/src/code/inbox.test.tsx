import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { inboxState, INBOX_STATES, type InboxSummary } from "./model";
import { InboxStatus, overviewRows, selectRange } from "./CodeTree";
import { RuntimeMark } from "../components/RuntimeMark";

const snapshot: InboxSummary = { state: "needs_attention", reason: "Checks failed", activity: "a".repeat(32), verification: "Checks failed", updated_at: 1, can_triage: true };

describe("Code inbox presentation", () => {
  it("keeps an approval visible even when the local turn still spins", () => {
    expect(inboxState(snapshot, true)).toBe("needs_attention");
    expect(inboxState({ ...snapshot, state: "snoozed" }, true)).toBe("working");
    expect(INBOX_STATES.length).toBe(5);
  });
  it("shows the evidence alongside review state without rendering source text as HTML", () => {
    const html = renderToStaticMarkup(<InboxStatus title="<img src=x>" inbox={snapshot} />);
    expect(html.includes("Needs attention · Checks failed")).toBe(true);
    expect(html.includes("&lt;img src=x&gt;")).toBe(true);
    expect(html.includes("<img")).toBe(false);
    expect(html.includes("Verified")).toBe(false);
  });
  it("keeps review readiness distinct from verification and supports older servers", () => {
    const html = renderToStaticMarkup(<InboxStatus title="Codex" inbox={{ ...snapshot, state: "ready_to_review", verification: "Runtime-owned checks" }} />);
    expect(html.includes("Ready to review · Runtime-owned checks")).toBe(true);
    expect(inboxState()).toBe("settled");
  });
  it("shares local provider images and never substitutes runtime initials", () => {
    const html=renderToStaticMarkup(<><RuntimeMark runtime="codex"/><RuntimeMark runtime="claude_cli"/></>);
    expect(html.includes('/runtime-marks/openai.svg')).toBe(true);
    expect(html.includes('/runtime-marks/claude.svg')).toBe(true);
    expect(html.includes('runtime-monogram')).toBe(false);
  });
  it("range selection uses visible order and the overview combines independent stores", () => {
    expect(selectRange(["a","b","c"],"c","a")).toEqual(["a","b","c"]);
    expect(selectRange(["b"],"hidden","b")).toEqual(["b"]);
    const rows=overviewRows({projects:[{id:"p",name:"App",workspace_root:"fixture",trust_state:"trusted",sessions:[],external:[{id:"external",runtime_id:"codex",state:"completed",created_at:10,project_id:"p",title:"Actual task"}]}],unorganized:[]});
    expect(rows[0].title).toBe("Actual task");expect(rows[0].external).toBe(true);expect(rows[0].project).toBe("App");
  });
});
