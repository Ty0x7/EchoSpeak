import { describe, expect, it } from "vitest";
import { RUNS, stepsFor } from "./runs";
import { reducer, type Player } from "./Workspace";

const start = (run: number): Player => ({ run, cursor: 0, t: 0, decision: null, playing: true });
const code = RUNS.findIndex((r) => r.id === "code");
const approvalAt = stepsFor(RUNS[code], null).findIndex((s) => s.kind === "approval");

describe("website run scripts", () => {
  it("are well formed", () => {
    for (const run of RUNS) {
      const ids = run.steps.map((s) => s.id);
      expect(new Set(ids).size).toBe(ids.length);
      for (const step of run.steps) {
        if (step.plan !== undefined) expect(step.plan < run.plan.length).toBe(true);
        if (step.kind === "handoff") expect(Boolean(step.to)).toBe(true);
        if (step.kind !== "approval") expect(step.ms > 0).toBe(true);
      }
      expect(run.steps[run.steps.length - 1].kind).toBe("answer");
    }
  });

  it("only adds the push after an Allow", () => {
    const names = (d: "allow" | "deny" | null) => stepsFor(RUNS[code], d).map((s) => s.id);
    expect(names(null).includes("c8")).toBe(false);
    expect(names("allow").includes("c8")).toBe(true);
    expect(names("deny").includes("c8")).toBe(false);
  });
});

describe("workspace player", () => {
  it("plays steps in order as time passes", () => {
    let s = start(0);
    const first = stepsFor(RUNS[0], null)[0];
    s = reducer(s, { type: "tick", dt: first.ms - 1 });
    expect(s.cursor).toBe(0);
    s = reducer(s, { type: "tick", dt: 1 });
    expect([s.cursor, s.t]).toEqual([1, 0]);
  });

  it("waits at an approval until the visitor answers", () => {
    let s: Player = { ...start(code), cursor: approvalAt };
    s = reducer(s, { type: "tick", dt: 60_000 });
    s = reducer(s, { type: "step" });
    expect(s.cursor).toBe(approvalAt);
    s = reducer(s, { type: "decide", decision: "allow" });
    expect([s.cursor, s.decision]).toEqual([approvalAt + 1, "allow"]);
    expect(stepsFor(RUNS[code], s.decision)[s.cursor].id).toBe("c8");
  });

  it("skips the push after a Deny and still finishes", () => {
    let s: Player = { ...start(code), cursor: approvalAt };
    s = reducer(s, { type: "decide", decision: "deny" });
    expect(stepsFor(RUNS[code], s.decision)[s.cursor].kind).toBe("answer");
    s = reducer(s, { type: "step" });
    expect(s.cursor).toBe(stepsFor(RUNS[code], "deny").length);
  });

  it("ignores answers when nothing is waiting, and restarts when finished", () => {
    const s = start(0);
    expect(reducer(s, { type: "decide", decision: "allow" })).toBe(s);
    const done: Player = { ...s, cursor: stepsFor(RUNS[0], null).length, playing: false };
    expect(reducer(done, { type: "toggle" })).toEqual({ run: 0, cursor: 0, t: 0, decision: null, playing: true });
    expect(reducer({ ...s, cursor: 3, decision: "deny" }, { type: "select", run: 2, play: false })).toEqual({ run: 2, cursor: 0, t: 0, decision: null, playing: false });
  });
});
