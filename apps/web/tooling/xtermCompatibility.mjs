/**
 * xterm 6's KeyCodeUtils namespace assigns an own `toString` using `=`.
 * Tauri freezes Object.prototype, making that inherited property read-only.
 * Define the namespace's own property instead; leave the frozen prototype intact.
 * This build-time adaptation keeps the pinned upstream package unmodified.
 */
export function adaptXtermForFrozenPrototype(source) {
  const assignment = /([\w$]+)\.toString=([\w$]+);/g;
  const matches = source.match(assignment) || [];
  if (matches.length !== 1) throw new Error("Review xterm's frozen-prototype compatibility: expected one namespace assignment");
  return source.replace(assignment, (_, target, value) =>
    `Object.defineProperty(${target},"toString",{value:${value},writable:true,configurable:true,enumerable:true});`);
}
