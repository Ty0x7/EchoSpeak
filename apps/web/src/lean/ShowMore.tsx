import React, { useState } from "react";

/** Long lists open on their first few items; "Show more" reveals the rest without hiding anything for good. */
export const PREVIEW_COUNT = 3;

export function useShowMore<T>(items: T[], activeIndex = -1) {
  // Start open when the selected item would otherwise be hidden.
  const [expanded, setExpanded] = useState(activeIndex >= PREVIEW_COUNT);
  const collapsible = items.length > PREVIEW_COUNT;
  const shown = expanded || !collapsible ? items : items.slice(0, PREVIEW_COUNT);
  return { shown, expanded, setExpanded, collapsible, hidden: items.length - shown.length };
}

export function ShowMore({ expanded, hidden, onToggle, label }: { expanded: boolean; hidden: number; onToggle(): void; label: string }) {
  return (
    <button
      type="button"
      className="es-show-more"
      aria-expanded={expanded}
      aria-label={expanded ? `Show fewer ${label}` : `Show ${hidden} more ${label}`}
      onClick={onToggle}
    >
      {expanded ? "Show less" : <>… Show more <span>{hidden}</span></>}
    </button>
  );
}

