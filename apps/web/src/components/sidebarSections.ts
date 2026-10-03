/** Layout state for the sidebar's resizable Agents / Chats / Projects stack. Pure helpers + storage. */

export type SectionKey = "agents" | "chats" | "projects";
export const SECTION_ORDER: SectionKey[] = ["agents", "chats", "projects"];

export type StackLayout = {
  /** Relative size of each section's list while open (any positive numbers; only ratios matter). */
  weights: Record<SectionKey, number>;
  open: Record<SectionKey, boolean>;
  /**
   * False until the user drags a divider or folds a section. Until then the
   * sizes are fitted to the content (fitLayout), so every new install starts
   * from the same tidy layout.
   */
  custom: boolean;
};

export const DEFAULT_STACK_LAYOUT: StackLayout = {
  weights: { agents: 0.26, chats: 0.44, projects: 0.3 },
  open: { agents: true, chats: true, projects: true },
  custom: false,
};
/** Height of one compact agent row (px), used to fit the Agents list. */
export const AGENT_ROW_PX = 33;
const AGENTS_FIT_MAX_ROWS = 6;
export const SECTION_SNAP_POINTS = [0.25, 0.5, 0.75];
/** Smallest list area (px) a section keeps while it and its neighbour are open. */
export const SECTION_MIN_PX = 76;
const SNAP_PX = 14;
// v3: older saved layouts (v1/v2) are ignored so everyone starts from the fitted default once.
const STORAGE_KEY = "echospeak.sidebar.stack.v3";

/**
 * Clamp the share of a pair (upper section's part of the two lists' combined height)
 * so both keep at least SECTION_MIN_PX, then snap near 25/50/75%.
 */
export function settleShare(share: number, areaPx: number, { snap = true } = {}): number {
  if (!Number.isFinite(share)) return 0.5;
  const minShare = areaPx > 0 ? Math.min(0.5, SECTION_MIN_PX / areaPx) : 0.15;
  let next = Math.min(1 - minShare, Math.max(minShare, share));
  if (snap && areaPx > 0) {
    const near = SECTION_SNAP_POINTS.find((point) => Math.abs(point - next) * areaPx <= SNAP_PX);
    if (near !== undefined && near >= minShare && near <= 1 - minShare) next = near;
  }
  return Math.round(next * 1000) / 1000;
}

/** Keyboard resize on a divider: arrows step 5%, Page keys 25%, Home/End to the limits. */
export function shareForKey(share: number, key: string, areaPx: number): number | null {
  const steps: Record<string, number> = { ArrowUp: -0.05, ArrowDown: 0.05, PageUp: -0.25, PageDown: 0.25 };
  if (key in steps) return settleShare(share + steps[key], areaPx, { snap: false });
  if (key === "Home") return settleShare(0, areaPx, { snap: false });
  if (key === "End") return settleShare(1, areaPx, { snap: false });
  return null;
}

/** Open sections in display order. */
export function openSections(layout: StackLayout, present: SectionKey[] = SECTION_ORDER): SectionKey[] {
  return present.filter((key) => layout.open[key]);
}

/** Each open section's fraction of the space the open lists share (sums to 1). */
export function sectionFractions(layout: StackLayout, present: SectionKey[] = SECTION_ORDER): Partial<Record<SectionKey, number>> {
  const open = openSections(layout, present);
  const total = open.reduce((sum, key) => sum + Math.max(0.001, layout.weights[key]), 0) || 1;
  return Object.fromEntries(open.map((key) => [key, Math.max(0.001, layout.weights[key]) / total]));
}

/** Upper section's share of the pair (a above b). */
export function pairShare(layout: StackLayout, a: SectionKey, b: SectionKey): number {
  const wa = Math.max(0.001, layout.weights[a]);
  const wb = Math.max(0.001, layout.weights[b]);
  return wa / (wa + wb);
}

/**
 * The default layout, fitted to what's in the sidebar: Agents just tall enough
 * for its rows (up to six, then it scrolls), Chats and Projects sharing the
 * rest about 60/40. A layout the user changed is returned untouched.
 */
export function fitLayout(layout: StackLayout, agentCount: number, areaPx: number): StackLayout {
  if (layout.custom || areaPx <= 0) return layout;
  const rows = Math.min(AGENTS_FIT_MAX_ROWS, Math.max(1, agentCount));
  const agents = Math.min(0.5, Math.max(0.12, (rows * AGENT_ROW_PX + 8) / areaPx));
  const rest = 1 - agents;
  return { ...layout, weights: { agents: round(agents), chats: round(rest * 0.62), projects: round(rest * 0.38) } };
}

/** Move the divider between a and b: the pair keeps its combined size, others are untouched. */
export function resizePair(layout: StackLayout, a: SectionKey, b: SectionKey, share: number): StackLayout {
  const combined = Math.max(0.002, layout.weights[a]) + Math.max(0.002, layout.weights[b]);
  const s = Math.min(1, Math.max(0, share));
  return { ...layout, custom: true, weights: { ...layout.weights, [a]: round(combined * s), [b]: round(combined * (1 - s)) } };
}

/** Double-click on a divider: back to the fitted default sizes (which sections are open stays). */
export function resetLayout(layout: StackLayout): StackLayout {
  return { ...DEFAULT_STACK_LAYOUT, open: { ...layout.open } };
}

export function toggleSection(layout: StackLayout, key: SectionKey): StackLayout {
  return { ...layout, custom: true, open: { ...layout.open, [key]: !layout.open[key] } };
}

const round = (value: number) => Math.round(value * 1000) / 1000;

function readLayout(raw: unknown): StackLayout {
  const value = raw as any;
  const weights = { ...DEFAULT_STACK_LAYOUT.weights };
  const open = { ...DEFAULT_STACK_LAYOUT.open };
  for (const key of SECTION_ORDER) {
    const w = Number(value?.weights?.[key]);
    if (Number.isFinite(w) && w > 0) weights[key] = Math.min(10, w);
    if (value?.open?.[key] === false) open[key] = false;
  }
  return { weights, open, custom: value?.custom === true };
}

export function loadStackLayout(): StackLayout {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) return readLayout(JSON.parse(raw));
  } catch {
    // Blocked or corrupt storage: fall back to the defaults.
  }
  return DEFAULT_STACK_LAYOUT;
}

export function saveStackLayout(layout: StackLayout): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // Private windows or blocked storage: the layout just won't persist.
  }
}
