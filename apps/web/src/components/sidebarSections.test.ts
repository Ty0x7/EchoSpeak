import { describe, expect, it } from "vitest";
import {
  DEFAULT_STACK_LAYOUT,
  SECTION_MIN_PX,
  fitLayout,
  pairShare,
  resetLayout,
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

  it("moves one divider without touching the other section, and remembers it as the user's", () => {
    const next = resizePair(DEFAULT_STACK_LAYOUT, "agents", "chats", 0.25);
    const before = DEFAULT_STACK_LAYOUT.weights;
    expect(next.weights.agents + next.weights.chats).toBeCloseTo(before.agents + before.chats, 3);
    expect(next.weights.projects).toBe(before.projects);
    expect(pairShare(next, "agents", "chats")).toBeCloseTo(0.25, 2);
    expect(next.custom).toBe(true);
    expect(resetLayout(next).custom).toBe(false);
  });

  it("starts every new install from the same fitted layout", () => {
    const fitted = fitLayout(DEFAULT_STACK_LAYOUT, 3, 600);
    expect(fitted.weights.agents).toBeCloseTo((3 * 33 + 8) / 600, 2); // three agent rows, no scrolling
    expect(fitted.weights.chats / fitted.weights.projects).toBeCloseTo(0.62 / 0.38, 2);
    expect(fitLayout(DEFAULT_STACK_LAYOUT, 23, 600).weights.agents).toBeCloseTo((6 * 33 + 8) / 600, 2); // capped at six rows
    const mine = resizePair(fitted, "chats", "projects", 0.3);
    expect(fitLayout(mine, 3, 600)).toBe(mine); // a layout the user changed is left alone
  });
});
