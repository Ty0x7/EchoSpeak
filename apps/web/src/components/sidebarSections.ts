/** Layout state for the sidebar's Chats / Projects split. Pure helpers + storage. */

export type SectionLayout = {
  /** Share of the split area given to Chats when both sections are open (0–1). */
  chatsShare: number;
  chatsOpen: boolean;
  projectsOpen: boolean;
};

export const DEFAULT_SECTION_LAYOUT: SectionLayout = { chatsShare: 0.5, chatsOpen: true, projectsOpen: true };
export const SECTION_SNAP_POINTS = [0.25, 0.5, 0.75];
/** Smallest list area (px) either section keeps while both are open. */
export const SECTION_MIN_PX = 76;
const SNAP_PX = 14;
const STORAGE_KEY = "echospeak.sidebar.sections.v1";

/** Clamp a Chats share so both lists keep at least SECTION_MIN_PX, then snap near 25/50/75%. */
export function settleShare(share: number, areaPx: number, { snap = true } = {}): number {
  if (!Number.isFinite(share)) return DEFAULT_SECTION_LAYOUT.chatsShare;
  const minShare = areaPx > 0 ? Math.min(0.5, SECTION_MIN_PX / areaPx) : 0.15;
  let next = Math.min(1 - minShare, Math.max(minShare, share));
  if (snap && areaPx > 0) {
    const near = SECTION_SNAP_POINTS.find((point) => Math.abs(point - next) * areaPx <= SNAP_PX);
    if (near !== undefined && near >= minShare && near <= 1 - minShare) next = near;
  }
  return Math.round(next * 1000) / 1000;
}

/** Keyboard resize on the divider: arrows step 5%, Page keys 25%, Home/End to the limits. */
export function shareForKey(share: number, key: string, areaPx: number): number | null {
  const steps: Record<string, number> = { ArrowUp: -0.05, ArrowDown: 0.05, PageUp: -0.25, PageDown: 0.25 };
  if (key in steps) return settleShare(share + steps[key], areaPx, { snap: false });
  if (key === "Home") return settleShare(0, areaPx, { snap: false });
  if (key === "End") return settleShare(1, areaPx, { snap: false });
  return null;
}

export function loadSectionLayout(): SectionLayout {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SECTION_LAYOUT;
    const value = JSON.parse(raw);
    return {
      chatsShare: typeof value?.chatsShare === "number" ? settleShare(value.chatsShare, 0, { snap: false }) : DEFAULT_SECTION_LAYOUT.chatsShare,
      chatsOpen: value?.chatsOpen !== false,
      projectsOpen: value?.projectsOpen !== false,
    };
  } catch {
    return DEFAULT_SECTION_LAYOUT;
  }
}

export function saveSectionLayout(layout: SectionLayout): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // Private windows or blocked storage: the layout just won't persist.
  }
}
