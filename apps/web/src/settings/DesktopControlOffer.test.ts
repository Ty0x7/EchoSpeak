import { describe, expect, it } from "vitest";
import { withDesktopControl } from "./DesktopControlOffer";

describe("offering desktop control", () => {
  it("adds control and screenshots without dropping or repeating the agent's toolsets", () => {
    expect(withDesktopControl(["core", "web", "live", "terminal", "vision", "memory", "skills"]))
      .toEqual(["core", "web", "live", "terminal", "vision", "memory", "skills", "desktop"]);
    expect(withDesktopControl(["web", "memory"])).toEqual(["web", "memory", "vision", "desktop"]);
    expect(withDesktopControl(["desktop", "vision"])).toEqual(["desktop", "vision"]);
  });
});
