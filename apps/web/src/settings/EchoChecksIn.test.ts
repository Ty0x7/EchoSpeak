import { describe, expect, it } from "vitest";
import { checkInMode } from "./SettingsPanel";

describe("Settings › Automations › Echo checks in", () => {
  it("reads one mode from the check-in setting and the friendly heartbeat", () => {
    expect(checkInMode({})).toBe("important");
    expect(checkInMode({ checkin_mode: "off" })).toBe("off");
    expect(checkInMode({ checkin_mode: "important", heartbeat_enabled: true })).toBe("friendly");
    expect(checkInMode({ checkin_mode: "off", heartbeat_enabled: "true" })).toBe("friendly");
  });
});
