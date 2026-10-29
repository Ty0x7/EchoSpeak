import { describe, expect, it } from "vitest";
import { agentModels, agentModelName, agentModelRow, canStartSession, CodeRequestError, createCodeSession, runtimeStatus, type CodeRuntime, type CodeWorkspace } from "./model";

const native: CodeRuntime = { id: "echo", name: "Echo native", protocol: "native", implemented: true, installed: true, authentication: "existing_model_settings", can_start: true, blockers: [], description: "" };
const workspace = { can_start_native: true } as CodeWorkspace;

describe("reported coding models", () => {
  it("keeps valid reported models and excludes malformed catalog entries", () => {
    expect(agentModels([null, { model: 7 }, { model: "" }, { id: "one", model: "native-model", displayName: "Native model", isDefault: true }]))
      .toEqual([{ id: "one", model: "native-model", displayName: "Native model", isDefault: true, efforts: [], defaultEffort: "" }]);
    expect(agentModels(null)).toEqual([]);
  });
  it("keeps each model's own effort levels and drops malformed ones (11.12)", () => {
    const [row] = agentModels([{ model: "gpt-x", efforts: ["low", "high", "<script>", 7, "x".repeat(30)], defaultEffort: "high" }]);
    expect(row.efforts).toEqual(["low", "high"]);
    expect(row.defaultEffort).toBe("high");
    const rows = agentModels([{ model: "a", isDefault: true, efforts: ["medium"] }, { model: "b", efforts: ["low"] }]);
    expect(agentModelRow(rows, "", "")?.model).toBe("a");
    expect(agentModelRow(rows, "b", "a")?.efforts).toEqual(["low"]);
  });
  it("uses selection, then reported model, then the advertised default without inventing one", () => {
    const models = [{ id: "a", model: "a", displayName: "Model A", isDefault: true }, { id: "b", model: "b", displayName: "Model B" }];
    expect(agentModelName(models, "b", "a")).toBe("Model B");
    expect(agentModelName(models, "", "b")).toBe("Model B");
    expect(agentModelName(models, "", "")).toBe("Model A");
    expect(agentModelName([], "", "")).toBe("");
  });
});

describe("Echo Code runtime admission", () => {
  it("requires a project, its trust gate, and a working native adapter", () => {
    expect(canStartSession(native, workspace)).toBe(true);
    expect(canStartSession(native, null)).toBe(false);
    expect(canStartSession(native, { ...workspace, can_start_native: false })).toBe(false);
    expect(canStartSession({ ...native, implemented: false }, workspace)).toBe(false);
    expect(canStartSession({ ...native, can_start: false }, workspace)).toBe(false);
  });
  it("cannot route an external runtime through the native query path", () => {
    for (const id of ["codex", "claude_cli", "acp"]) expect(canStartSession({ ...native, id }, workspace)).toBe(false);
  });
  it("does not turn a discovered CLI into an authenticated or executable integration", () => {
    expect(runtimeStatus({ ...native, id: "codex", implemented: false })).toBe("CLI found · execution planned");
    expect(runtimeStatus({ ...native, implemented: false, installed: false }).includes("CLI not found")).toBe(true);
    expect(runtimeStatus({ ...native, implemented: false, installed: null })).toBe("Planned");
  });
  it("sends the selected runtime and retry identity to the separate creation endpoint", async () => {
    const original = globalThis.fetch;
    let requestedUrl = ""; let requestedBody = "";
    globalThis.fetch = async (url, options) => {
      requestedUrl = String(url); requestedBody = String(options?.body);
      return new Response(JSON.stringify({ thread_id: "code-1" }));
    };
    try {
      expect(await createCodeSession("http://local", "project/a", "echo", "same-request")).toEqual({ thread_id: "code-1" });
      expect(requestedUrl).toBe("http://local/echo-code/projects/project%2Fa/sessions");
      expect(JSON.parse(requestedBody)).toEqual({ runtime_id: "echo", idempotency_key: "same-request" });
    } finally { globalThis.fetch = original; }
  });
  it("retains the HTTP status so rejected requests can release their retry key", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Runtime unavailable" }), { status: 409 });
    try {
      let rejected = false;
      try { await createCodeSession("", "p", "codex", "key"); }
      catch (err) {
        rejected = true;
        expect(err instanceof CodeRequestError).toBe(true);
        expect((err as CodeRequestError).status).toBe(409);
        expect((err as Error).message).toBe("Runtime unavailable");
      }
      expect(rejected).toBe(true);
    } finally { globalThis.fetch = original; }
  });
});
