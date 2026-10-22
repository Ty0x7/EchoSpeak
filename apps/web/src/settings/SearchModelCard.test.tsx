import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { activeLine, buttonLabel, changed, downloadSize, SearchModelCard, type SearchStatus } from "./SearchModelCard";

const status = (over: Partial<SearchStatus> = {}): SearchStatus => ({
  selected: "fast",
  runtime_available: true,
  models: [
    { key: "fast", label: "Fast · English", size_mb: 90, languages: "English", installed: true },
    { key: "better", label: "Better · 100+ languages, longer text", size_mb: 220, languages: "100+ languages", installed: false },
  ],
  reranker: { label: "Sharper document results", size_mb: 24, installed: false, enabled: false },
  lmstudio_models: [],
  lmstudio_model: "",
  openai_ready: false,
  active: { available: true, provider: "onnx_local", model: "sentence-transformers/all-MiniLM-L6-v2", label: "Fast · English" },
  ...over,
});

describe("search by meaning", () => {
  it("downloads only what isn't on this PC yet", () => {
    expect(downloadSize(status(), "better", false)).toBe(220);
    expect(downloadSize(status(), "fast", false)).toBe(0);
    expect(downloadSize(status(), "better", true)).toBe(244);
    expect(downloadSize(status(), "lmstudio", false)).toBe(0);
  });

  it("names the button by what it will do", () => {
    expect(buttonLabel(status(), "better", false, false)).toBe("Download (220 MB) and switch");
    expect(buttonLabel(status(), "off", false, false)).toBe("Switch");
    expect(buttonLabel(status(), "better", false, true)).toBe("Downloading and re-indexing…");
  });

  it("only offers a switch when something changed", () => {
    expect(changed(status(), "fast", "", false)).toBe(false);
    expect(changed(status(), "better", "", false)).toBe(true);
    expect(changed(status(), "fast", "", true)).toBe(true);
    expect(changed(status({ selected: "lmstudio", lmstudio_model: "a" }), "lmstudio", "b", false)).toBe(true);
  });

  it("says plainly what search is using", () => {
    expect(activeLine(status())).toEqual({ tone: "ok", text: "Using Fast · English" });
    expect(activeLine(status({ selected: "off" })).text).toBe("Off: keyword search only");
    expect(activeLine(status({ selected: "better", active: { available: false, detail: "The local embedding model (Better) is not downloaded" } })).tone).toBe("warn");
    expect(activeLine(status({ selected: "lmstudio", active: { available: true, provider: "lm_studio", model: "text-embedding-qwen3-embedding-0.6b" } })).text)
      .toBe("Using LM Studio · text-embedding-qwen3-embedding-0.6b");
  });

  it("renders one card with every choice, the reranker switch and a plain status", () => {
    const html = renderToStaticMarkup(<SearchModelCard apiBase="http://api" initial={status()} />);
    for (const text of ["Search by meaning", "Keywords only", "Fast · English", "Better · 100+ languages, longer text",
      "Recommended", "Use LM Studio", "Sharper results for English documents", "Using Fast · English", "220 MB"]) {
      expect(html.includes(text)).toBe(true);
    }
    expect(html.includes("Cloud · OpenAI")).toBe(false); // no key: not offered
    expect(html.includes("Load an embedding model in LM Studio first")).toBe(true);
  });

  it("lists LM Studio's embedding models when it is chosen", () => {
    const html = renderToStaticMarkup(<SearchModelCard apiBase="http://api" initial={status({
      selected: "lmstudio", lmstudio_model: "text-embedding-qwen3-embedding-0.6b",
      lmstudio_models: [{ id: "text-embedding-qwen3-embedding-0.6b", loaded: true }, { id: "text-embedding-nomic-embed-text-v1.5", loaded: false }],
    })} />);
    expect(html.includes("text-embedding-qwen3-embedding-0.6b (loaded)")).toBe(true);
    expect(html.includes("text-embedding-nomic-embed-text-v1.5")).toBe(true);
  });
});
