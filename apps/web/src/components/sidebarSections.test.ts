import { describe, expect, it } from "vitest";
import { SECTION_MIN_PX, settleShare, shareForKey } from "./sidebarSections";

describe("sidebar section split", () => {
  it("keeps both lists at least the minimum height", () => {
    const area = 400;
    expect(settleShare(0.02, area)).toBe(SECTION_MIN_PX / area);
    expect(settleShare(0.99, area)).toBe(1 - SECTION_MIN_PX / area);
  });

  it("snaps near 25/50/75% but not further away", () => {
    expect(settleShare(0.52, 400)).toBe(0.5); // 8px from the 50% point
    expect(settleShare(0.74, 400)).toBe(0.75);
    expect(settleShare(0.6, 400)).toBe(0.6); // 40px away: no snap
  });

  it("falls back to an even split for bad input", () => {
    expect(settleShare(Number.NaN, 400)).toBe(0.5);
  });

  it("supports keyboard resizing of the divider", () => {
    expect(shareForKey(0.5, "ArrowDown", 400)).toBe(0.55);
    expect(shareForKey(0.5, "ArrowUp", 400)).toBe(0.45);
    expect(shareForKey(0.5, "Home", 400)).toBe(SECTION_MIN_PX / 400);
    expect(shareForKey(0.5, "End", 400)).toBe(1 - SECTION_MIN_PX / 400);
    expect(shareForKey(0.5, "Enter", 400)).toBeNull();
  });
});
