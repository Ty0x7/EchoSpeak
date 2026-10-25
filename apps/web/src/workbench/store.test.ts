import { describe, expect, it } from "vitest";
import { contextKey, previewUrl, useWorkbench } from "./store";

function reset() {
  useWorkbench.setState({ context: { project_id: "project-a", external_session_id: "" }, tabs: [], active: "", rightOpen: false, bottomOpen: false, detailsOpen: false, terminalIds: {} });
}

describe("workspace identity and surfaces", () => {
  it("clears resource tabs when the project or owned agent worktree changes", () => {
    reset();
    useWorkbench.getState().open("file", { path: "src/app.ts", title: "app.ts" });
    useWorkbench.getState().setContext({ project_id: "project-a", external_session_id: "owned-session" });
    expect(useWorkbench.getState().tabs).toEqual([]);
    expect(useWorkbench.getState().active).toBe("");
  });
  it("does not duplicate tabs and keeps adjacent tabs after closing", () => {
    reset();
    const state = useWorkbench.getState();
    state.open("files"); state.open("files"); state.open("diff"); state.close("diff");
    expect(useWorkbench.getState().tabs.map(tab => tab.id)).toEqual(["files"]);
    expect(useWorkbench.getState().active).toBe("files");
  });
  it("keeps terminal responses bound to the context where creation began", () => {
    reset();
    const original = useWorkbench.getState().context;
    useWorkbench.getState().setContext({ project_id: "project-b", external_session_id: "" });
    useWorkbench.getState().addTerminal("terminal-a", original);
    expect(useWorkbench.getState().terminalIds[contextKey(original)]).toEqual(["terminal-a"]);
    expect(useWorkbench.getState().terminalIds[contextKey(useWorkbench.getState().context)]).toBe(undefined);
  });
  it("opens the surface chooser when showing an empty sidebar", () => {
    reset();
    useWorkbench.getState().toggleRight();
    expect(useWorkbench.getState().rightOpen).toBe(true);
    expect(useWorkbench.getState().active).toBe("surfaces");
  });
});

describe("opaque local preview boundary", () => {
  it("permits only loopback dev servers on a different port from the API", () => {
    expect(previewUrl("http://127.0.0.1:3000", "http://localhost:8000")).toBe("http://127.0.0.1:3000/");
    for (const value of ["http://127.0.0.1:8000/settings", "http://localhost:8000", "https://example.com", "javascript:alert(1)", "file:///C:/secret", "http://user:secret@localhost:3000"]) {
      expect(previewUrl(value, "http://127.0.0.1:8000")).toBeNull();
    }
  });
});
