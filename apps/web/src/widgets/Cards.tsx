import React, { useEffect, useState } from "react";
import { ExternalLink, RemoteImage, openExternal } from "./env";
import type { CitationsData, MediaData, ProductData, ScoreData } from "./types";
import { hostOf } from "./validate";

const money = (price: number, currency = "USD") => {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD" }).format(price);
  } catch {
    return `$${price.toFixed(2)}`;
  }
};

function Stars({ rating, reviews }: { rating: number; reviews?: number | null }) {
  const full = Math.round(rating * 2) / 2;
  return (
    <span className="wg-stars" aria-label={`Rated ${rating.toFixed(1)} out of 5${reviews ? `, ${reviews} reviews` : ""}`}>
      <span aria-hidden>{"★".repeat(Math.floor(full))}{full % 1 ? "½" : ""}</span>
      <small>{rating.toFixed(1)}{reviews ? ` (${reviews.toLocaleString()})` : ""}</small>
    </span>
  );
}

export function ProductCarousel({ data }: { data: ProductData }) {
  return (
    <section className="wg wg-products" aria-label={data.query ? `Products for ${data.query}` : "Products"}>
      <div className="wg-scroller" role="list">
        {data.items.map((item) => (
          <article key={item.url} className="wg-product" role="listitem">
            <ExternalLink href={item.url} className="wg-product-link" title={item.title}>
              <RemoteImage src={item.image} alt="" className="wg-product-img" fit="contain" />
              <span className="wg-product-title">{item.title}</span>
            </ExternalLink>
            <div className="wg-product-meta">
              <strong>{money(item.price, item.currency)}</strong>
              <span>{item.merchant || hostOf(item.url)}</span>
            </div>
            {item.rating ? <Stars rating={item.rating} reviews={item.reviews} /> : <span className="wg-stars is-empty">No rating</span>}
            <button type="button" className="wg-btn" onClick={() => openExternal(item.url)}>View at {item.merchant || hostOf(item.url)}</button>
          </article>
        ))}
      </div>
      <footer className="wg-foot">Prices from the store pages{data.as_of ? `, as of ${data.as_of}` : ""}. They can change; check the store before buying.</footer>
    </section>
  );
}

function Lightbox({ items, index, onClose, onMove }: { items: MediaData["items"]; index: number; onClose(): void; onMove(next: number): void }) {
  const item = items[index];
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowRight") onMove((index + 1) % items.length);
      if (event.key === "ArrowLeft") onMove((index - 1 + items.length) % items.length);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, items.length, onClose, onMove]);
  return (
    <div className="wg-lightbox" role="dialog" aria-modal="true" aria-label={item.title || "Image"} onClick={onClose}>
      <div className="wg-lightbox-body" onClick={(event) => event.stopPropagation()}>
        <RemoteImage src={item.image || item.thumbnail} alt={item.title || ""} fit="contain" className="wg-lightbox-img" />
        <div className="wg-lightbox-bar">
          <span>{item.title}</span>
          <ExternalLink href={item.url}>{item.publisher || hostOf(item.url)} ↗</ExternalLink>
          <span className="wg-lightbox-count">{index + 1} / {items.length}</span>
          <button type="button" className="wg-ghost" onClick={() => onMove((index - 1 + items.length) % items.length)} aria-label="Previous image">‹</button>
          <button type="button" className="wg-ghost" onClick={() => onMove((index + 1) % items.length)} aria-label="Next image">›</button>
          <button type="button" className="wg-ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
      </div>
    </div>
  );
}

export function MediaGallery({ data }: { data: MediaData }) {
  const [open, setOpen] = useState<number | null>(null);
  if (data.kind === "video") {
    return (
      <section className="wg wg-videos" aria-label={data.query ? `Videos for ${data.query}` : "Videos"}>
        <div className="wg-scroller" role="list">
          {data.items.map((item) => (
            <ExternalLink key={item.url} href={item.url} className="wg-video" title={item.title}>
              <span className="wg-video-thumb">
                <RemoteImage src={item.thumbnail} alt="" />
                <span className="wg-video-play" aria-hidden>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l11-6.5z" /></svg>
                </span>
                {item.duration ? <span className="wg-video-time">{item.duration}</span> : null}
              </span>
              <span className="wg-video-title">{item.title}</span>
              <small>{[item.publisher || hostOf(item.url), item.published].filter(Boolean).join(" · ")}</small>
            </ExternalLink>
          ))}
        </div>
      </section>
    );
  }
  return (
    <section className="wg wg-images" aria-label={data.query ? `Images of ${data.query}` : "Images"}>
      <div className="wg-image-grid">
        {data.items.map((item, i) => (
          <button key={`${item.url}-${i}`} type="button" className="wg-image-tile" onClick={() => setOpen(i)} title={item.title}>
            <RemoteImage src={item.thumbnail} alt={item.title || ""} />
          </button>
        ))}
      </div>
      {open !== null ? <Lightbox items={data.items} index={open} onClose={() => setOpen(null)} onMove={setOpen} /> : null}
    </section>
  );
}

/** Numbered source chips at the end of an answer (several searches merged). */
export function CitationChips({ data }: { data: CitationsData }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? data.items : data.items.slice(0, 5);
  return (
    <section className="wg-sources" aria-label="Sources">
      <span className="wg-sources-label">Sources</span>
      {shown.map((item, i) => (
        <ExternalLink key={item.url} href={item.url} className="wg-source" title={[item.title, item.snippet].filter(Boolean).join("\n")}>
          <b>{i + 1}</b>
          <span>{item.site || hostOf(item.url)}</span>
        </ExternalLink>
      ))}
      {data.items.length > 5 ? (
        <button type="button" className="wg-source is-more" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Fewer" : `+${data.items.length - 5} more`}
        </button>
      ) : null}
    </section>
  );
}

export function ScoreCard({ data }: { data: ScoreData }) {
  return (
    <section className="wg wg-scores" aria-label={data.title || "Scores"}>
      {data.title ? <header className="wg-head"><figcaption>{data.title}</figcaption></header> : null}
      <div className="wg-score-list">
        {data.games.map((g, i) => {
          const final = g.home_score != null && g.away_score != null;
          const awayWins = final && (g.away_score as number) > (g.home_score as number);
          const homeWins = final && (g.home_score as number) > (g.away_score as number);
          return (
            <div key={i} className="wg-score">
              <div className={`wg-score-team${awayWins ? " is-win" : ""}`}><span>{g.away}</span><b>{g.away_score ?? ""}</b></div>
              <div className={`wg-score-team${homeWins ? " is-win" : ""}`}><span>{g.home}</span><b>{g.home_score ?? ""}</b></div>
              <small>{[g.league, g.status || g.start].filter(Boolean).join(" · ")}</small>
            </div>
          );
        })}
      </div>
      {data.as_of ? <footer className="wg-foot">As of {data.as_of}</footer> : null}
    </section>
  );
}
