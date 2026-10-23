import { describe, expect, it } from "vitest";
import { applyFeed } from "./reminderFeed";
import { describeSchedule } from "../settings/SettingsPanel";

const item = (seq: number, session_id: string) => ({ seq, title: "⏰ Reminder", body: "Raptors vs Timberwolves", session_id, kind: "reminder" });

describe("reminder feed", () => {
  it("only learns where the feed is on the first poll", () => {
    expect(applyFeed(-1, { items: [item(1, "a")], latest: 1 }, "a", false)).toEqual({ latest: 1, refresh: [], notify: [] });
  });

  it("refreshes the open chat and notifies in a browser", () => {
    const step = applyFeed(1, { items: [item(2, "a"), item(3, "b")], latest: 3 }, "a", false);
    expect(step.latest).toBe(3);
    expect(step.refresh).toEqual(["a"]);
    expect(step.notify.map((i) => i.seq)).toEqual([2, 3]);
  });

  it("leaves notifications to the desktop shell in the desktop app", () => {
    expect(applyFeed(1, { items: [item(2, "a")], latest: 2 }, "a", true).notify).toEqual([]);
  });
});

describe("reminders on the Routines page", () => {
  it("says when a one-time reminder fires, and when it's done", () => {
    const base = { trigger_type: "once", schedule: null, kind: "reminder", run_at: "2026-10-25T16:00:00-06:00" };
    expect(describeSchedule({ ...base, enabled: true }).startsWith("Once · ")).toBe(true);
    expect(describeSchedule({ ...base, enabled: false })).toBe("Once · done");
    expect(describeSchedule({ trigger_type: "schedule", schedule: "0 16 * * 0", enabled: true, kind: "reminder", metadata: { repeat: "weekly" } }))
      .toBe("Repeats weekly");
  });
});
