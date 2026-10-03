import { describe, expect, it } from "vitest";
import { AT_BOTTOM_PX, ChatFollower, distanceFromBottom } from "./chatFollow";

/** A scroll container: content grows as messages stream in; the browser clamps scrollTop. */
class Box {
  scrollTop = 0;
  scrollHeight: number;
  clientHeight = 600;

  constructor(height = 600) {
    this.scrollHeight = height;
  }

  grow(px: number) {
    this.scrollHeight += px;
  }

  shrink(px: number) {
    this.scrollHeight -= px;
    this.scrollTop = Math.min(this.scrollTop, Math.max(0, this.scrollHeight - this.clientHeight));
  }

  scrollTo(top: number) {
    this.scrollTop = Math.max(0, Math.min(top, this.scrollHeight - this.clientHeight));
  }
}

/** What the chat view does on every content change: pin if following, then the scroll event fires. */
function contentChanged(follower: ChatFollower, box: Box) {
  if (follower.shouldPin()) {
    const before = box.scrollTop;
    box.scrollTo(box.scrollHeight);
    if (box.scrollTop !== before) follower.onScroll(box);
  }
}

function stream(follower: ChatFollower, box: Box, chunks: number, px = 24) {
  for (let i = 0; i < chunks; i += 1) {
    box.grow(px);
    contentChanged(follower, box);
  }
}

describe("chat auto-follow", () => {
  it("follows streaming text and rapid messages while the user is at the bottom", () => {
    const follower = new ChatFollower();
    const box = new Box(1200);
    contentChanged(follower, box);
    stream(follower, box, 200, 7); // token stream
    stream(follower, box, 40, 160); // rapid whole messages (a group run)
    expect(distanceFromBottom(box)).toBe(0);
    expect(follower.following).toBe(true);
  });

  it("stops following the moment the user wheels up, even mid-stream, and keeps their place", () => {
    const follower = new ChatFollower();
    const box = new Box(3000);
    contentChanged(follower, box);
    follower.onWheel(-120, box); // fires before the browser moves the view
    expect(follower.shouldPin()).toBe(false); // a pin already scheduled won't undo the scroll
    box.scrollTo(box.scrollTop - 120);
    follower.onScroll(box);
    const reading = box.scrollTop;
    stream(follower, box, 100, 30); // agents keep talking
    expect(box.scrollTop).toBe(reading);
    expect(follower.following).toBe(false);
  });

  it("a small drag up inside the bottom slack still counts as scrolling up", () => {
    const follower = new ChatFollower();
    const box = new Box(3000);
    contentChanged(follower, box);
    box.scrollTo(box.scrollTop - 12); // scrollbar drag: no wheel event
    follower.onScroll(box);
    expect(distanceFromBottom(box) < AT_BOTTOM_PX).toBe(true);
    expect(follower.following).toBe(false);
    stream(follower, box, 5);
    expect(distanceFromBottom(box) > 12).toBe(true);
  });

  it("resumes when the user scrolls back down near the bottom", () => {
    const follower = new ChatFollower();
    const box = new Box(3000);
    contentChanged(follower, box);
    follower.onWheel(-400, box);
    box.scrollTo(box.scrollTop - 400);
    follower.onScroll(box);
    stream(follower, box, 10);
    box.scrollTo(box.scrollHeight - box.clientHeight - 20); // within the slack, moving down
    follower.onScroll(box);
    expect(follower.following).toBe(true);
    stream(follower, box, 10);
    expect(distanceFromBottom(box)).toBe(0);
  });

  it("content that shrinks (a retracted reply) is not mistaken for scrolling up", () => {
    const follower = new ChatFollower();
    const box = new Box(3000);
    contentChanged(follower, box);
    box.shrink(200); // the browser clamps scrollTop down
    follower.onScroll(box);
    expect(follower.following).toBe(true);
    stream(follower, box, 3);
    expect(distanceFromBottom(box)).toBe(0);
  });

  it("never pins under a finger, and decides on release", () => {
    const follower = new ChatFollower();
    const box = new Box(3000);
    contentChanged(follower, box);
    follower.onTouchStart();
    const held = box.scrollTop;
    stream(follower, box, 5);
    expect(box.scrollTop).toBe(held);
    follower.onTouchEnd(box);
    expect(follower.following).toBe(false); // released away from the new bottom
    box.scrollTo(box.scrollHeight);
    follower.onTouchStart();
    follower.onTouchEnd(box);
    expect(follower.following).toBe(true);
  });

  it("keys that move up stop following; a short chat with nothing to scroll keeps following", () => {
    const follower = new ChatFollower();
    const box = new Box(3000);
    follower.onKey("PageUp", box);
    expect(follower.following).toBe(false);
    const short = new ChatFollower();
    const small = new Box(400);
    short.onWheel(-100, small);
    expect(short.following).toBe(true);
  });

  it("sending a message follows its reply again", () => {
    const follower = new ChatFollower();
    const box = new Box(3000);
    follower.onWheel(-100, box);
    follower.reset(true, box);
    stream(follower, box, 3);
    expect(distanceFromBottom(box)).toBe(0);
  });
});
