import { describe, expect, it } from "vitest";
import { adaptXtermForFrozenPrototype } from "../../tooling/xtermCompatibility.mjs";

describe("xterm under Tauri prototype protection", () => {
  it("shadows a frozen inherited method without modifying the prototype", () => {
    const parent = Object.freeze({ toString: () => "protected" });
    const namespace = Object.create(parent);
    const source = '"use strict";target.toString=format;';
    const run = new Function("target", "format", adaptXtermForFrozenPrototype(source));
    run(namespace, () => "terminal");
    expect(namespace.toString()).toBe("terminal");
    expect(parent.toString()).toBe("protected");
    expect(Object.getOwnPropertyDescriptor(namespace, "toString")?.enumerable).toBe(true);
    namespace.toString = () => "updated";
    expect(namespace.toString()).toBe("updated");
  });
  it("requires review when an upstream change no longer matches the adaptation", () => {
    for (const source of ["new upstream layout", "a.toString=b;c.toString=d;"]) {
      let refused = false;
      try { adaptXtermForFrozenPrototype(source); } catch { refused = true; }
      expect(refused).toBe(true);
    }
  });
});
