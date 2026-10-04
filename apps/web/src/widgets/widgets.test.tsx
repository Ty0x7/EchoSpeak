import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { fileNameFor } from "./ArtifactPanel";
import { formatValue, niceTicks } from "./Chart";
import { parseFence } from "./CodeBlock";
import { compareCells } from "./DataTable";
import { WidgetEnvProvider } from "./env";
import { RichMarkdown } from "./RichMarkdown";
import { SafeImage } from "./SafeImage";
import { safeUrl, validateWidget, widgetFromFence } from "./validate";
import { WidgetView } from "./WidgetView";

const render = (node: React.ReactNode) => renderToStaticMarkup(<WidgetEnvProvider value={{ apiBase: "http://api" }}>{node}</WidgetEnvProvider>);

describe("widget validation", () => {
  it("keeps only http(s) URLs", () => {
    expect(safeUrl("https://a.example/x")).toBe("https://a.example/x");
    expect(safeUrl("javascript:alert(1)")).toBe("");
    expect(safeUrl("data:image/png;base64,AA")).toBe("");
    expect(safeUrl("/relative")).toBe("");
    expect(safeUrl(42)).toBe("");
  });

  it("rejects malformed tool cards instead of crashing", () => {
    expect(validateWidget(null)).toBe(null);
    expect(validateWidget({ type: "weather", data: { current: {} } })).toBe(null);
    expect(validateWidget({ type: "product_carousel", data: { items: [{ title: "x", url: "ftp://a", price: 3 }] } })).toBe(null);
    expect(validateWidget({ type: "made_up", data: {} })).toBe(null);
    const media = validateWidget({ type: "media", data: { kind: "video", items: [{ url: "https://v.example", thumbnail: "javascript:x" }, { url: "https://v.example/2", thumbnail: "https://t.example/2.jpg" }] } });
    expect(media && media.type === "media" ? media.data.items.length : 0).toBe(1);
  });

  it("parses model-written blocks and falls back on bad JSON or schema", () => {
    expect(widgetFromFence("chart", "{not json")).toBe(null);
    expect(widgetFromFence("chart", JSON.stringify({ labels: [], series: [] }))).toBe(null);
    const shorthand = widgetFromFence("chart", JSON.stringify({ kind: "bar", data: [{ label: "A", value: 3 }, { label: "B", value: "4" }] }));
    expect(shorthand?.type).toBe("chart");
    expect(shorthand && shorthand.type === "chart" ? shorthand.data.series[0].values : []).toEqual([3, 4]);
    expect(widgetFromFence("comparison", JSON.stringify({ items: [{ name: "only one", specs: {} }] }))).toBe(null);
    const steps = widgetFromFence("steps", JSON.stringify({ items: [{ title: "One" }, { name: "Two" }, { detail: "no title" }] }));
    expect(steps && steps.type === "timeline" ? [steps.data.ordered, steps.data.items.length] : null).toEqual([true, 2]);
    expect(widgetFromFence("map", JSON.stringify({ places: [{ name: "Nowhere", lat: 200, lon: 0 }] }))).toBe(null);
  });
});

describe("widget rendering", () => {
  it("requires a click for protocol-relative and local proxy images in model text", () => {
    for (const src of ["https://outside.example/a.png", "//outside.example/a.png", "/lean/media?url=https%3A%2F%2Foutside.example%2Fa.png"]) {
      const html = renderToStaticMarkup(<SafeImage src={src} alt="remote" />);
      expect(html.includes("<img")).toBe(false);
      expect(html.includes("Show image")).toBe(true);
    }
    expect(renderToStaticMarkup(<SafeImage src="data:image/png;base64,AA" alt="inline" />).includes("<img")).toBe(true);
  });
  it("draws a weather card even with missing forecast fields", () => {
    const html = render(<WidgetView widget={{ type: "weather", data: { location: "Denver, Colorado", units: "F", current: { temp: 59.4, code: 0 }, hourly: [], daily: [{ date: "2026-10-03", max: 83, min: 50, code: 61 }] } }} />);
    expect(html.includes("Denver, Colorado")).toBe(true);
    expect(html.includes("59°")).toBe(true);
    expect(html.includes("Today")).toBe(true);
  });

  it("draws charts (with gaps) and offers a table view", () => {
    const html = render(<WidgetView widget={{ type: "chart", data: { kind: "line", title: "NVDA vs AMD", labels: ["2026-01-02", "2026-01-03", "2026-01-04"], series: [{ name: "NVDA", values: [0, null, 5] }, { name: "AMD", values: [0, 2, 9] }], unit: "%", source: "Yahoo Finance" } }} />);
    expect(html.includes("NVDA vs AMD")).toBe(true);
    expect(html.includes("Table")).toBe(true);
    expect(html.includes("Yahoo Finance")).toBe(true);
  });

  it("shows product cards with prices and a placeholder for missing images", () => {
    const html = render(<WidgetView widget={{ type: "product_carousel", data: { items: [{ title: "Pad", url: "https://shop.example/p", price: 29.99, merchant: "shop.example" }], as_of: "Oct 2" } }} />);
    expect(html.includes("$29.99")).toBe(true);
    expect(html.includes("wg-img-missing")).toBe(true);
    expect(html.includes("as of Oct 2")).toBe(true);
  });

  it("proxies images through the backend", () => {
    const html = render(<WidgetView widget={{ type: "media", data: { kind: "image", items: [{ url: "https://p.example", thumbnail: "https://img.example/a.jpg", title: "A" }] } }} />);
    expect(html.includes("http://api/lean/media?url=https%3A%2F%2Fimg.example%2Fa.jpg")).toBe(true);
  });

  it("shows video cards with durations", () => {
    const html = render(<WidgetView widget={{ type: "media", data: { kind: "video", items: [{ url: "https://youtube.com/watch?v=x", thumbnail: "https://i.ytimg.com/x.jpg", title: "SMD soldering", duration: "19:14", publisher: "Mr SolderFix" }] } }} />);
    expect(html.includes("19:14")).toBe(true);
    expect(html.includes("SMD soldering")).toBe(true);
  });

  it("renders the other blocks", () => {
    expect(render(<WidgetView widget={{ type: "citations", data: { items: [{ url: "https://a.example/x", title: "A" }] } }} />).includes("a.example")).toBe(true);
    expect(render(<WidgetView widget={{ type: "score_card", data: { games: [{ home: "Nuggets", away: "Lakers", home_score: 110, away_score: 102, status: "Final" }] } }} />).includes("Nuggets")).toBe(true);
    expect(render(<WidgetView widget={{ type: "stat", data: { items: [{ label: "Rate", value: "1.08", change: "+0.4%" }] } }} />).includes("▲")).toBe(true);
    expect(render(<WidgetView widget={{ type: "map", data: { places: [{ name: "Union Station", lat: 39.75, lon: -105 }] } }} />).includes("Union Station")).toBe(true);
    expect(render(<WidgetView widget={{ type: "comparison", data: { items: [{ name: "A", specs: { Price: "$1" } }, { name: "B", specs: { Weight: "2kg" } }] } }} />).includes("Weight")).toBe(true);
    expect(render(<WidgetView widget={{ type: "artifact", data: { id: "abc123def456", title: "Tip calculator", kind: "html", version: 2 } }} />).includes("v2")).toBe(true);
  });

  it("renders nothing for garbage", () => {
    expect(render(<WidgetView widget={{ type: "chart", data: "nope" }} />)).toBe("");
    expect(render(<WidgetView widget={42} />)).toBe("");
  });
});

describe("rich markdown", () => {
  it("turns fenced blocks into widgets and falls back to code when invalid", () => {
    const good = render(<RichMarkdown text={'Here:\n\n```chart\n{"kind":"bar","labels":["A","B"],"series":[{"name":"x","values":[1,2]}]}\n```'} />);
    expect(good.includes("wg-chart")).toBe(true);
    const bad = render(<RichMarkdown text={"```chart\n{oops\n```"} />);
    expect(bad.includes("wg-code")).toBe(true);
    expect(bad.includes("wg-chart")).toBe(false);
  });

  it("renders math, diagrams, tables, code and safe links", () => {
    const html = render(<RichMarkdown text={"$$E = mc^2$$\n\n```mermaid\ngraph TD; A-->B\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```py title=\"app.py\"\nprint(1)\n```\n\nPrice is $5 and $10. [site](https://example.com) [bad](javascript:alert(1))"} />);
    expect(html.includes("katex")).toBe(true);
    expect(html.includes("Drawing diagram")).toBe(true);
    expect(html.includes("wg-datatable")).toBe(true);
    expect(html.includes("app.py")).toBe(true);
    expect(html.includes("Price is $5 and $10.")).toBe(true); // single $ is not math
    expect(html.includes('href="https://example.com/"')).toBe(true);
    expect(html.includes("javascript:")).toBe(false);
  });

  it("waits for a streaming block before drawing it", () => {
    expect(render(<RichMarkdown text={'```chart\n{"labels": ["A"'} streaming />).includes("Preparing chart")).toBe(true);
  });
});

describe("helpers", () => {
  it("sorts numbers numerically and text naturally", () => {
    expect(compareCells("$9", "$10") < 0).toBe(true);
    expect(compareCells("item 2", "item 10") < 0).toBe(true);
  });
  it("makes nice axis ticks and labels", () => {
    expect(niceTicks(0, 9)).toEqual([0, 2.5, 5, 7.5, 10]);
    expect(formatValue(12.3, "%")).toBe("+12.3%");
    expect(formatValue(null)).toBe("–");
  });
  it("reads file names from fences", () => {
    expect(parseFence("ts", 'title="src/app.ts"')).toEqual({ language: "ts", filename: "src/app.ts" });
    expect(parseFence("py:main.py", "")).toEqual({ language: "py", filename: "main.py" });
  });
  it("names downloads by kind", () => {
    expect(fileNameFor({ title: "Tip Calculator!", kind: "html", language: "" })).toBe("tip-calculator.html");
    expect(fileNameFor({ title: "Script", kind: "code", language: "python" })).toBe("script.py");
  });
});
