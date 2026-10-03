import React from "react";
import { Comparison, MapCard, StatTiles, Timeline } from "./Blocks";
import { CitationChips, MediaGallery, ProductCarousel, ScoreCard } from "./Cards";
import { ChartView } from "./Chart";
import { useWidgetEnv } from "./env";
import type { ArtifactRef, Widget } from "./types";
import { validateWidget } from "./validate";
import { WeatherCard } from "./Weather";
import "./widgets.css";

/** One broken widget never takes the message down: it falls back to its plain-text fallback. */
class WidgetBoundary extends React.Component<{ fallback: React.ReactNode; children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.warn("Widget failed to render; showing text instead.", error);
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

const KIND_LABEL: Record<string, string> = { html: "App", svg: "SVG image", mermaid: "Diagram", markdown: "Document", code: "Code" };

export function ArtifactCard({ data }: { data: ArtifactRef }) {
  const { openArtifact } = useWidgetEnv();
  return (
    <button type="button" className="wg wg-artifact" onClick={() => openArtifact?.(data.id, data.version)} disabled={!openArtifact}>
      <span className="wg-artifact-icon" aria-hidden>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          {data.kind === "html" ? <path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14" /> : data.kind === "mermaid" ? <path d="M5 5h5v5H5zM14 14h5v5h-5zM7.5 10v4.5h6.5" /> : <path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20zM14 3.5V8h4M9.5 12h5M9.5 15.5h5" />}
        </svg>
      </span>
      <span className="wg-artifact-text">
        <strong>{data.title}</strong>
        <small>{KIND_LABEL[data.kind] || data.kind}{data.language ? ` · ${data.language}` : ""} · v{data.version}</small>
      </span>
      <span className="wg-artifact-open">Open</span>
    </button>
  );
}

/** A plain-text fallback for any widget, used when it can't be drawn. */
function fallbackText(widget: Widget): string {
  switch (widget.type) {
    case "weather":
      return `${widget.data.location}: ${Math.round(widget.data.current.temp)}°${widget.data.units}`;
    case "product_carousel":
      return widget.data.items.map((i) => `${i.title} - ${i.price}`).join("\n");
    case "media":
    case "citations":
      return widget.data.items.map((i) => `${i.title || ""} ${i.url}`).join("\n");
    default:
      return "";
  }
}

export function WidgetView({ widget }: { widget: unknown }) {
  const checked = validateWidget(widget);
  if (!checked) return null;
  const fallback = fallbackText(checked);
  const body = (() => {
    switch (checked.type) {
      case "weather":
        return <WeatherCard data={checked.data} />;
      case "chart":
        return <ChartView data={checked.data} />;
      case "product_carousel":
        return <ProductCarousel data={checked.data} />;
      case "media":
        return <MediaGallery data={checked.data} />;
      case "citations":
        return <CitationChips data={checked.data} />;
      case "score_card":
        return <ScoreCard data={checked.data} />;
      case "timeline":
        return <Timeline data={checked.data} />;
      case "comparison":
        return <Comparison data={checked.data} />;
      case "stat":
        return <StatTiles data={checked.data} />;
      case "map":
        return <MapCard data={checked.data} />;
      case "artifact":
        return <ArtifactCard data={checked.data} />;
      default:
        return null;
    }
  })();
  return <WidgetBoundary fallback={fallback ? <pre className="wg-fallback">{fallback}</pre> : null}>{body}</WidgetBoundary>;
}
