/**
 * Conservative fixes for the Mermaid mistakes models make most often. The original is always
 * tried first; a repaired copy is only drawn when the original doesn't parse, and the Source
 * view still shows what the model wrote. Pure string work: no rendering, so it's unit-tested.
 *
 * Measured against Mermaid 11 (docs/research/chat-harness.md): unquoted ( ) / : in labels,
 * `A --> B: text` edge labels, unicode arrows, a node called `end`, and prose after the diagram.
 */

const FLOWCHART = /^\s*(?:flowchart|graph)\b/i;
// Characters that end or confuse a bare flowchart label.
const RISKY = /[()[\]{}/\\:;|<>#&"]/;
// id + simple shape: A[label], A(label), A{label}. Skip shapes Mermaid already parses: [(…)], [/…/], ((…)), {{…}}, ([…]), [[…]].
const NODE = /([A-Za-z_][\w-]*)(\[(?![([])|\((?![(\[])|\{(?!\{))([^"\n]*?)(\]|\)|\})(?=\s|$|;|-|=|&|:::)/g;
const CLOSE: Record<string, string> = { "[": "]", "(": ")", "{": "}" };

function quoteLabels(line: string): string {
  return line.replace(NODE, (whole, id: string, open: string, label: string, close: string) => {
    if (CLOSE[open] !== close || !RISKY.test(label)) return whole;
    // [/text/] and [\text\] are Mermaid's trapezoid shapes, already valid.
    if (open === "[" && /^[/\\].*[/\\]$/.test(label)) return whole;
    return `${id}${open}"${label.replace(/"/g, "'")}"${close}`;
  });
}

/** Fix one flowchart line; other diagram types are left alone. */
function repairFlowLine(line: string): string {
  const arrows = line.replace(/\s*(?:→|⟶|➔|➜)\s*/g, " --> ").replace(/\s*(?:←|⟵)\s*/g, " <-- ");
  // Quote risky labels first, then hide quoted text so the edge rules never look inside a label.
  const saved: string[] = [];
  let next = quoteLabels(arrows).replace(/"[^"]*"/g, (quoted) => `\u0000${saved.push(quoted) - 1}\u0000`);
  // `A --> B: label` (a sequence-diagram habit) → `A -->|label| B`
  next = next.replace(/^(\s*\S+)\s*(-->|---|-\.->|==>)\s*([^\s:|]+)\s*:\s*(.+)$/,
    (_w, from: string, arrow: string, to: string, label: string) => `${from} ${arrow}|${label.trim().replace(/\|/g, "/")}| ${to}`);
  // A node named `end` (a keyword) on an edge line
  if (/-->|---|==>|-\.->/.test(next)) next = next.replace(/(^|[\s>|-])end(?=\s*($|[;\s[({-]))/g, "$1End");
  return next.replace(/\u0000(\d+)\u0000/g, (_w, index: string) => saved[Number(index)]);
}

/** Whether a line looks like Mermaid flowchart syntax rather than a sentence. */
function looksLikeDiagramLine(line: string): boolean {
  const text = line.trim();
  if (!text) return true;
  if (/^(%%|subgraph\b|end\b|style\b|classDef\b|class\b|click\b|linkStyle\b|direction\b)/.test(text)) return true;
  if (/-->|---|==>|-\.->|<--|→|\|/.test(text)) return true;
  // A lone node declaration: id, id[label], id(label), id{label}
  return /^[A-Za-z_][\w-]*\s*(\[.*\]|\(.*\)|\{.*\})?\s*;?$/.test(text) && text.split(/\s+/).length <= 6;
}

export function repairMermaid(source: string): string {
  const lines = source.replace(/\r\n?/g, "\n").replace(/^\n+|\s+$/g, "").split("\n");
  const start = lines.findIndex(line => line.trim() && !line.trim().startsWith("%%") && !line.trim().startsWith("---"));
  if (start < 0 || !FLOWCHART.test(lines[start])) return lines.join("\n");
  const body = lines.slice(start + 1);
  // Prose after the diagram ("This shows the flow.") isn't Mermaid: drop trailing non-diagram lines.
  while (body.length && !looksLikeDiagramLine(body[body.length - 1])) body.pop();
  return [...lines.slice(0, start + 1), ...body.map(repairFlowLine)].join("\n");
}

/** Mermaid's parse errors are multi-line (message, the line, a caret, what it expected): keep the useful part. */
export function mermaidErrorDetail(error: unknown): { summary: string; detail: string; kind: "syntax" | "render" } {
  const text = String((error as { message?: string })?.message || error || "Mermaid couldn't parse this diagram.").trim();
  const lines = text.split("\n").filter(Boolean);
  // "render": parsed fine, then Mermaid itself threw (not the model's mistake).
  const kind = (error as { mermaidKind?: string })?.mermaidKind === "render" ? "render" : "syntax";
  return { summary: lines[0].slice(0, 200), detail: lines.slice(1, 4).join("\n").slice(0, 600), kind };
}
