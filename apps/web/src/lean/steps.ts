import type { LeanMessageData, LeanSegment } from "./types";

/**
 * How an agent's work reads in the chat: consecutive tool steps fold into one
 * line ("Searched the web ×3 · Read 5 pages · Checked prices"), and between
 * steps the agent says what it is doing ("Looking over the results…").
 */

export type ToolSeg = Extract<LeanSegment, { kind: "tool" }>;
export type StepItem = { kind: "steps"; tools: ToolSeg[]; key: string } | { kind: "seg"; seg: LeanSegment; index: number };

const blank = (seg: LeanSegment) => seg.kind === "text" && !seg.text.trim();

/** Segments in order, with each run of consecutive tool steps gathered into one item. */
export function groupSteps(segments: LeanSegment[]): StepItem[] {
  const items: StepItem[] = [];
  segments.forEach((seg, index) => {
    if (blank(seg)) return;
    const last = items[items.length - 1];
    if (seg.kind === "tool") {
      if (last && last.kind === "steps") last.tools.push(seg);
      else items.push({ kind: "steps", tools: [seg], key: `s${seg.id || index}` });
      return;
    }
    items.push({ kind: "seg", seg, index });
  });
  return items;
}

type Bucket = { order: number; count: number; label: (n: number) => string };

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const times = (n: number) => (n > 1 ? ` ×${n}` : "");

/** Which line a tool counts toward, in the order the lines read. */
function bucketOf(name: string): [string, number, (n: number) => string] | null {
  switch (name) {
    case "web_search": return ["search", 0, (n) => `Searched the web${times(n)}`];
    case "safe_web_fetch": return ["pages", 1, (n) => `Read ${plural(n, "page")}`];
    case "product_search": return ["prices", 2, (n) => `Checked prices${times(n)}`];
    case "image_search": return ["images", 3, () => "Found pictures"];
    case "video_search": return ["videos", 3, () => "Found videos"];
    case "file_read": return ["read", 4, (n) => `Read ${plural(n, "file")}`];
    case "file_list": case "file_find": case "file_search": return ["look", 5, (n) => `Looked through files${times(n)}`];
    case "file_write": case "file_edit": case "file_move": case "file_copy": case "file_mkdir": case "file_delete":
      return ["change", 6, (n) => `Changed ${plural(n, "file")}`];
    case "terminal": case "terminal_run": case "process_start": case "process_output": case "process_stop":
      return ["run", 7, (n) => `Ran ${plural(n, "command")}`];
    case "memory_search": case "memory_save": return ["memory", 8, () => "Checked memory"];
    case "create_artifact": case "update_artifact": return ["artifact", 9, (n) => (n > 1 ? `Built ${n} artifacts` : "Built an artifact")];
    case "delegate_to_agent": return ["team", 10, () => "Asked a teammate"];
    default: return null;
  }
}

/** "Searched the web ×3 · Read 5 pages · Checked prices" for the finished steps in a run. */
export function stepsSummary(tools: ToolSeg[]): string {
  const buckets = new Map<string, Bucket>();
  let other = 0;
  let failed = 0;
  const add = (key: string, order: number, label: (n: number) => string, n = 1) => {
    const bucket = buckets.get(key) || { order, count: 0, label };
    bucket.count += n;
    buckets.set(key, bucket);
  };
  for (const tool of tools) {
    if (tool.status === "running") continue;
    if (tool.status === "failed") failed += 1;
    const found = bucketOf(tool.name);
    if (found) add(...found);
    else other += 1;
    // Pages web_search read for itself count as pages read.
    const pages = Number(tool.meta?.pages || 0);
    if (tool.name === "web_search" && pages) add("pages", 1, (n) => `Read ${plural(n, "page")}`, pages);
  }
  const parts = [...buckets.values()].sort((a, b) => a.order - b.order).map((b) => b.label(b.count));
  if (other) parts.push(plural(other, "other step"));
  if (failed) parts.push(`${failed} failed`);
  return parts.join(" · ");
}

/** What the agent is doing between visible steps, or "" when a step or the answer already shows it. */
export function phaseOf(msg: LeanMessageData): string {
  if (msg.status !== "streaming") return "";
  const segments = msg.segments.filter((s) => !blank(s));
  const last = segments[segments.length - 1];
  if (!last) return msg.role === "merge" ? "Pulling it together…" : "Getting started…";
  if (last.kind === "thinking") return "Thinking it through…";
  if (last.kind === "approval") return last.decision ? "Carrying on…" : "";
  if (last.kind === "text" || last.kind === "note") return "";
  if (last.status === "running") return "";
  // A step just finished: say what comes next, from what it was.
  const kind = bucketOf(last.name)?.[0] || "";
  if (kind === "search" || kind === "pages" || kind === "prices" || kind === "images" || kind === "videos") return "Looking over the results…";
  if (kind === "run") return last.status === "failed" ? "Working out what went wrong…" : "Checking the output…";
  if (kind === "change" || kind === "artifact") return "Checking the changes…";
  if (kind === "read" || kind === "look") return "Reading through it…";
  if (kind === "team") return "Waiting on the team…";
  return "Deciding what to do next…";
}
