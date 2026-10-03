/**
 * Chat auto-follow: keep the newest message in view while the user is at the
 * bottom, and leave the view alone once they scroll up to read.
 *
 * Decided from what the user does, not from which scroll events we caused:
 * the chat only ever scrolls itself down, so any upward movement (wheel,
 * scrollbar drag, keys, touch) is the user. An upward wheel stops following
 * before the scroll even happens, so a pin that lands mid-scroll can't undo it.
 * Content that shrinks clamps the view to the very bottom, which is not the
 * user scrolling up. Coming back within a few lines of the bottom resumes.
 */

/** How close to the end still counts as "at the bottom". */
export const AT_BOTTOM_PX = 48;

const UP_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);

export type ScrollBox = { scrollTop: number; scrollHeight: number; clientHeight: number };

export function distanceFromBottom(box: ScrollBox): number {
  return Math.max(0, box.scrollHeight - box.scrollTop - box.clientHeight);
}

function scrollable(box: ScrollBox): boolean {
  return box.scrollHeight - box.clientHeight > 1;
}

export class ChatFollower {
  following = true;
  private lastTop = 0;
  private touching = false;

  constructor(private readonly threshold: number = AT_BOTTOM_PX) {}

  /** Any scroll event, whoever caused it. */
  onScroll(box: ScrollBox): void {
    const top = box.scrollTop;
    const dist = distanceFromBottom(box);
    const movedUp = top < this.lastTop - 1;
    if (movedUp && dist > 2) {
      this.following = false;
    } else if (!movedUp && !this.touching && dist <= this.threshold) {
      this.following = true;
    }
    this.lastTop = top;
  }

  /** An upward wheel is the intent to read back, even before the view moves. */
  onWheel(deltaY: number, box: ScrollBox): void {
    if (deltaY < 0 && scrollable(box)) this.following = false;
  }

  onKey(key: string, box: ScrollBox): void {
    if (UP_KEYS.has(key) && scrollable(box)) this.following = false;
  }

  /** Never pin under a finger. */
  onTouchStart(): void {
    this.touching = true;
  }

  onTouchEnd(box: ScrollBox): void {
    this.touching = false;
    this.following = distanceFromBottom(box) <= this.threshold;
    this.lastTop = box.scrollTop;
  }

  /** Should the chat move itself to the newest content now? */
  shouldPin(): boolean {
    return this.following && !this.touching;
  }

  /** A deliberate jump (sending a message, opening a chat): follow or not, from here. */
  reset(following: boolean, box?: ScrollBox): void {
    this.following = following;
    this.touching = false;
    if (box) this.lastTop = box.scrollTop;
  }
}
