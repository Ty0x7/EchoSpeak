import { describe, expect, it } from "vitest";
import {
  DEFAULT_STACK_LAYOUT,
  SECTION_MIN_PX,
  migrateLegacyLayout,
  pairShare,
  resetPair,
  resizePair,
  sectionFractions,
  settleShare,
  shareForKey,
  toggleSection,
} from "./sidebarSections";

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

describe("sidebar stack (Agents / Chats / Projects)", () => {
  it("gives open sections fractions that fill the space", () => {
    const f = sectionFractions(DEFAULT_STACK_LAYOUT);
    expect((f.agents || 0) + (f.chats || 0) + (f.projects || 0)).toBeCloseTo(1, 5);
    const closed = toggleSection(DEFAULT_STACK_LAYOUT, "chats");
    const g = sectionFractions(closed);
    expect(g.chats).toBe(undefined);
    expect((g.agents || 0) + (g.projects || 0)).toBeCloseTo(1, 5);
  });

  it("moves one divider without touching the other section", () => {
    const next = resizePair(DEFAULT_STACK_LAYOUT, "agents", "chats", 0.25);
    const before = DEFAULT_STACK_LAYOUT.weights;
    expect(next.weights.agents + next.weights.chats).toBeCloseTo(before.agents + before.chats, 3);
    expect(next.weights.projects).toBe(before.projects);
    expect(pairShare(next, "agents", "chats")).toBeCloseTo(0.25, 2);
    expect(pairShare(resetPair(next, "agents", "chats"), "agents", "chats")).toBeCloseTo(pairShare(DEFAULT_STACK_LAYOUT, "agents", "chats"), 2);
  });

  it("keeps the old Chats/Projects split when upgrading", () => {
    const layout = migrateLegacyLayout({ chatsShare: 0.75, chatsOpen: true, projectsOpen: false });
    expect(pairShare(layout, "chats", "projects")).toBeCloseTo(0.75, 2);
    expect(layout.open.projects).toBe(false);
    expect(layout.open.agents).toBe(true);
  });
});
