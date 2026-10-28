import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ResponseRenderer } from "../features/responseRenderer/ResponseRenderer";
import { endsInOpenFence, normalizeMathDelimiters } from "./markdownPrep";
import { mermaidErrorDetail, repairMermaid } from "./mermaidRepair";
import { RichMarkdown } from "./RichMarkdown";

// Real drawing is checked in a real browser (apps/desktop/scripts/check-chat-render-browser.cjs);
// these pin the pure steps around it.
describe("Mermaid repair: the mistakes models make most", () => {
  it("quotes labels with punctuation and fixes edges, arrows and keywords", () => {
    expect(repairMermaid("flowchart TD\n  A[User (you)] --> B[Chat]")).toBe('flowchart TD\n  A["User (you)"] --> B[Chat]');
    expect(repairMermaid("flowchart TD\n  A --> B: answer")).toBe("flowchart TD\n  A -->|answer| B");
    expect(repairMermaid("flowchart TD\n  A → B")).toBe("flowchart TD\n  A --> B");
    expect(repairMermaid("flowchart TD\n  start --> end")).toBe("flowchart TD\n  start --> End");
    expect(repairMermaid("flowchart TD\n  A[/api/chat] --> B")).toBe('flowchart TD\n  A["/api/chat"] --> B');
    expect(repairMermaid("graph LR\n  API{Route: /chat} --> C(Cache: redis)")).toBe('graph LR\n  API{"Route: /chat"} --> C("Cache: redis")');
  });

  it("drops prose after the diagram and leaves valid shapes and other diagram types alone", () => {
    expect(repairMermaid("flowchart TD\n  A-->B\nThis shows the flow.")).toBe("flowchart TD\n  A-->B");
    const shapes = "flowchart TD\n  A([Start]) --> B[[Sub]] --> C((Circle)) --> D>Flag] --> E{{Hex}} --> F[(DB)] --> G[/In/]";
    expect(repairMermaid(shapes)).toBe(shapes);
    const sequence = "sequenceDiagram\n  User->>Echo: Hello (there)";
    expect(repairMermaid(sequence)).toBe(sequence);
    expect(repairMermaid('flowchart TD\n  A["Already (quoted)"] -->|yes: ok| B')).toBe('flowchart TD\n  A["Already (quoted)"] -->|yes: ok| B');
  });

  it("tells a renderer crash on valid input apart from a syntax mistake", () => {
    // Live test: "Cannot read properties of undefined (reading 'valueOf')" was shown as a syntax error.
    const crash = Object.assign(new TypeError("Cannot read properties of undefined (reading 'valueOf')"), { mermaidKind: "render" });
    expect(mermaidErrorDetail(crash).kind).toBe("render");
    expect(mermaidErrorDetail(Object.assign(new Error("Parse error on line 2:"), { mermaidKind: "syntax" })).kind).toBe("syntax");
  });

  it("keeps the useful part of Mermaid's multi-line parse errors", () => {
    const detail = mermaidErrorDetail(new Error("Parse error on line 2:\n...A[[[ -->\n-----^\nExpecting 'TAGEND', got 'SQS'\nmore"));
    expect(detail.summary).toBe("Parse error on line 2:");
    expect(detail.detail).toBe("...A[[[ -->\n-----^\nExpecting 'TAGEND', got 'SQS'");
  });
});

describe("Streaming Markdown preparation", () => {
  it("reads ChatGPT-style \\( \\) and \\[ \\] math, never inside code, and leaves prices alone", () => {
    expect(normalizeMathDelimiters("Area is \\(\\pi r^2\\) here.")).toBe("Area is $$\\pi r^2$$ here.");
    expect(normalizeMathDelimiters("Then\n\\[\nE = mc^2\n\\]\ndone")).toBe("Then\n\n$$\nE = mc^2\n$$\n\ndone");
    expect(normalizeMathDelimiters("```latex\n\\(x\\)\n```")).toBe("```latex\n\\(x\\)\n```");
    expect(normalizeMathDelimiters("Use `\\(x\\)` literally")).toBe("Use `\\(x\\)` literally");
    expect(normalizeMathDelimiters("It costs $5 and $10.")).toBe("It costs $5 and $10.");
  });

  it("knows when the reply is still inside a code block", () => {
    expect(endsInOpenFence("Text\n```mermaid\nflowchart TD\n  A-->")).toBe(true);
    expect(endsInOpenFence("Text\n```mermaid\nflowchart TD\n  A-->B\n```\nMore text")).toBe(false);
    expect(endsInOpenFence("~~~\ncode\n```\nstill code")).toBe(true);
  });
});

describe("Rich rendering everywhere", () => {
  it("renders math, tables and diagrams in Chat replies", () => {
    const html = renderToStaticMarkup(<RichMarkdown text={"Inline \\(a^2\\).\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```mermaid\nflowchart TD\n  A-->B\n```"} />);
    expect(html.includes("katex")).toBe(true);
    expect(html.includes("<table")).toBe(true);
    expect(html.includes("wg-diagram")).toBe(true);
  });

  it("renders a reply shown from its final text the same way (it used to be plain Markdown)", () => {
    const html = renderToStaticMarkup(<ResponseRenderer fallbackText={"```mermaid\nflowchart TD\n  A-->B\n```\n\nCost \\(O(n)\\)."} colors={{} as never} />);
    expect(html.includes("wg-diagram")).toBe(true);
    expect(html.includes("katex")).toBe(true);
    expect(html.includes("```mermaid")).toBe(false);
  });
});
