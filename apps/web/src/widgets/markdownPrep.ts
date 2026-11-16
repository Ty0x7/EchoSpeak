/**
 * Small, pure preparations for streamed Markdown (unit-tested in widgets.test.tsx).
 */

const FENCE = /^ {0,3}(`{3,}|~{3,})/;

/** Split text into code (fenced blocks) and prose, keeping order. An unclosed fence runs to the end. */
function segments(text: string): { code: boolean; text: string }[] {
  const out: { code: boolean; text: string }[] = [];
  const lines = text.split("\n");
  let buffer: string[] = [];
  let fence = "";
  const flush = (code: boolean) => {
    if (buffer.length) out.push({ code, text: buffer.join("\n") });
    buffer = [];
  };
  for (const line of lines) {
    const match = FENCE.exec(line);
    if (!fence && match) {
      flush(false);
      fence = match[1];
      buffer.push(line);
    } else if (fence && match && match[1][0] === fence[0] && match[1].length >= fence.length && !line.trim().slice(match[1].length).trim()) {
      buffer.push(line);
      flush(true);
      fence = "";
    } else {
      buffer.push(line);
    }
  }
  flush(Boolean(fence));
  return out;
}

/** Whether the text ends inside a code fence (the model is still writing that block). */
export function endsInOpenFence(text: string): boolean {
  let fence = "";
  for (const line of text.split("\n")) {
    const match = FENCE.exec(line);
    if (!fence && match) fence = match[1];
    else if (fence && match && match[1][0] === fence[0] && match[1].length >= fence.length && !line.trim().slice(match[1].length).trim()) fence = "";
  }
  return Boolean(fence);
}

/**
 * Models trained on ChatGPT-style output write math as \( … \) and \[ … \]. remark-math only
 * reads dollar delimiters, so convert those (never inside code). A single $ stays plain text,
 * so prices like $5 are safe; \( … \) becomes $$…$$, which remark-math renders inline.
 */
export function normalizeMathDelimiters(text: string): string {
  if (!text.includes("\\(") && !text.includes("\\[")) return text;
  return segments(text).map((part) => {
    if (part.code) return part.text;
    // Leave inline code spans alone: split on backtick runs, convert only the prose pieces.
    return part.text.split(/(`+[^`]*`+)/g).map((piece, index) => {
      if (index % 2 === 1) return piece;
      return piece
        .replace(/\\\[([\s\S]+?)\\\]/g, (_w, body: string) => `\n$$\n${body.trim()}\n$$\n`)
        .replace(/\\\(([^\n]+?)\\\)/g, (_w, body: string) => `$$${body.trim()}$$`);
    }).join("");
  }).join("\n");
}
