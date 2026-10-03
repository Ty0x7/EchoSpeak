import React, { useState } from "react";
import { settleShare, shareForKey } from "./sidebarSections";

export const SPLIT_HANDLE_PX = 9;

export type SplitGeometry = {
  /** Screen y where the upper section's list starts. */
  top: number;
  /** Combined height (layout px) of the two lists the handle divides. */
  area: number;
  /** Screen px per layout px (the shell may be zoomed). */
  scale: number;
};

/**
 * The divider between two sidebar sections: drag, arrow keys, or double-click to
 * reset. Every divider in the sidebar is this component, so they all behave alike.
 */
export function SplitHandle({
  label,
  share,
  measure,
  onShare,
  onReset,
  onDragging,
}: {
  label: string;
  /** Upper section's share of the pair (0–1). */
  share: number;
  measure(): SplitGeometry;
  onShare(share: number): void;
  onReset(): void;
  onDragging?(dragging: boolean): void;
}) {
  const [dragging, setDragging] = useState(false);
  const setDrag = (value: boolean) => {
    setDragging(value);
    onDragging?.(value);
  };
  return (
    <div
      className="es-split-handle"
      role="separator"
      aria-orientation="horizontal"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(share * 100)}
      data-dragging={dragging ? "true" : "false"}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      onPointerDown={(event) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        setDrag(true);
      }}
      onPointerMove={(event) => {
        if (!dragging) return;
        const { top, area, scale } = measure();
        const y = (event.clientY - top) / (scale || 1) - SPLIT_HANDLE_PX / 2;
        onShare(settleShare(y / (area || 1), area));
      }}
      onPointerUp={(event) => {
        event.currentTarget.releasePointerCapture(event.pointerId);
        setDrag(false);
      }}
      onPointerCancel={() => setDrag(false)}
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        const next = shareForKey(share, event.key, measure().area);
        if (next === null) return;
        event.preventDefault();
        onShare(next);
      }}
    >
      <span aria-hidden />
    </div>
  );
}
