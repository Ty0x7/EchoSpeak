import React, { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** A viewport-bounded picker shared by the coding and native chat composers. */
export function WorkspacePopover({ anchor, label, width = 340, onClose, children, className = "" }: {
  anchor: React.RefObject<HTMLElement>; label: string; width?: number; onClose(): void;
  children: React.ReactNode; className?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 12, top: 12, width, ready: false });
  useLayoutEffect(() => {
    const place = () => {
      if (!anchor.current || !panel.current) return;
      const rect = anchor.current.getBoundingClientRect();
      const boundedWidth = Math.min(width, window.innerWidth - 24);
      const height = Math.min(panel.current.scrollHeight, window.innerHeight - 24);
      const above = rect.top - 8;
      const below = window.innerHeight - rect.bottom - 8;
      const top = above >= height || above > below ? rect.top - height - 8 : rect.bottom + 8;
      setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - boundedWidth - 12)),
        top: Math.max(12, Math.min(top, window.innerHeight - height - 12)), width: boundedWidth, ready: true });
    };
    const outside = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) onClose();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); anchor.current?.focus(); }
    };
    place();
    if (panel.current && !panel.current.contains(document.activeElement)) {
      panel.current.querySelector<HTMLElement>('[role="combobox"], [aria-checked="true"], [aria-pressed="true"], button:not(:disabled)')?.focus();
    }
    const observer = new ResizeObserver(place);
    if (panel.current) observer.observe(panel.current);
    window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    window.addEventListener("pointerdown", outside); window.addEventListener("keydown", escape, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true);
      window.removeEventListener("pointerdown", outside); window.removeEventListener("keydown", escape, true); };
  }, [anchor, width, onClose]);
  return createPortal(<div ref={panel} className={`es-floating-popover ${className}`} role="dialog" aria-label={label}
    onKeyDown={event => {
      if (event.defaultPrevented || (event.target as HTMLElement).tagName === "INPUT") return;
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      const options = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || []);
      if (!options.length) return;
      event.preventDefault();
      const current = options.indexOf(document.activeElement as HTMLButtonElement);
      const index = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
        : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options[index].focus();
    }}
    style={{ left: position.left, top: position.top, width: position.width, visibility: position.ready ? "visible" : "hidden" }}>
    {children}
  </div>, document.body);
}
