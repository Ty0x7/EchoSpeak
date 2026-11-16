import React from "react";
export function SidebarIcon({ name }: { name: "search" | "filter" | "compose" | "folder" | "branch" | "connections" }) {
  return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {name === "search" ? <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></> : name === "filter" ? <path d="M4 6h16M7 12h10M10 18h4"/> : name === "compose" ? <><path d="M13 5H5v14h14v-8M12 12l8-8M15 4h5v5"/></> : name === "folder" ? <><path d="M3 7h7l2 2h9v11H3zM3 7V4h7l2 3"/><path d="M12 12v5M9.5 14.5h5"/></> : name === "branch" ? <><circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M18 7v2c0 4-12 2-12 6"/></> : <><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01M17 7h1M17 17h1"/></>}
  </svg>;
}
