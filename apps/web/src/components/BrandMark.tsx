import React from "react";
import { BRAND_MARKS } from "./brandMarks";

/** Relative luminance of a #rrggbb color (WCAG). */
function luminance(hex: string): number {
  const [r, g, b] = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** A connector's logo; very dark brand colors take the text color so they show in both themes. */
export function BrandMark({ id, name, size = 22 }: { id: string; name: string; size?: number }) {
  const mark = BRAND_MARKS[id];
  if (!mark) {
    return <span className="brand-mark brand-mark-letter" style={{ width: size, height: size, fontSize: Math.round(size * 0.5) }} aria-hidden>
      {String(name || "?").trim().charAt(0).toUpperCase()}</span>;
  }
  const color = luminance(mark.hex) < 0.04 ? "currentColor" : `#${mark.hex}`;
  return <svg className="brand-mark" width={size} height={size} viewBox="0 0 24 24" role="img" aria-label={`${mark.title} logo`}>
    <path d={mark.path} fill={color} /></svg>;
}
