/** Research posts: Markdown files in apps/web/content/research with a small front-matter header. */

export type PostStatus = "verified" | "measured" | "negative" | "hypothesis" | "method" | "philosophy";

export interface Post {
  slug: string;
  title: string;
  date: string;
  summary: string;
  status: PostStatus;
  tags: string[];
  evidence: string[];
  body: string;
}

export const STATUS_LABEL: Record<PostStatus, string> = {
  verified: "Verified finding",
  measured: "Measured on one machine",
  negative: "Negative result",
  hypothesis: "Hypothesis",
  method: "Method",
  philosophy: "Research direction",
};

const files = import.meta.glob("../../../content/research/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

function parseList(value: string): string[] {
  const inner = value.trim().replace(/^\[/, "").replace(/\]$/, "");
  return inner ? inner.split(",").map((s) => s.trim()).filter(Boolean) : [];
}

function parse(raw: string): Post {
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(raw);
  const head: Record<string, string> = {};
  if (match) {
    for (const line of match[1].split("\n")) {
      const at = line.indexOf(":");
      if (at > 0) head[line.slice(0, at).trim()] = line.slice(at + 1).trim().replace(/^"(.*)"$/, "$1").replace(/\\"/g, '"');
    }
  }
  return {
    slug: head.slug || "",
    title: head.title || "",
    date: head.date || "",
    summary: head.summary || "",
    status: (head.status as PostStatus) || "method",
    tags: parseList(head.tags || ""),
    evidence: parseList(head.evidence || ""),
    body: match ? match[2] : raw,
  };
}

/** Reading order: the five founding posts in the order a newcomer should read them. */
const ORDER = ["accessible-hardware", "same-model-better-harness", "bonsai-27b-on-8gb", "evidence-based-execution", "research-methodology"];

export const POSTS: Post[] = Object.values(files)
  .map(parse)
  .filter((p) => p.slug)
  .sort((a, b) => (ORDER.indexOf(a.slug) + 1 || 99) - (ORDER.indexOf(b.slug) + 1 || 99) || b.date.localeCompare(a.date));

export function findPost(slug: string | undefined): Post | undefined {
  return POSTS.find((p) => p.slug === slug);
}

export const REPO_BLOB = "https://github.com/Ty0x7/EchoSpeak/blob/main/";
