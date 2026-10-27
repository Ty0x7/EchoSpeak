import React from "react";
export function WorkbenchIcon({ kind }: { kind: string }) {
  const paths: Record<string, React.ReactNode> = {
    details: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 7h8M8 11h8M8 15h6" /></>,
    bottom: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 14h18" /></>,
    right: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></>,
    terminal: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 8 3 3-3 3m6 1h4" /></>,
    files: <><path d="M3 6h7l2 3h9v11H3z" /><path d="M3 6V4h7l2 2h6" /></>,
    file: <><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6" /></>,
    diff: <><path d="M6 3h8l4 4v14H6zM9 12h6m-3-3v6M9 18h6" /></>,
    browser: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c6 5 6 13 0 18-6-5-6-13 0-18" /></>,
    pull_requests: <><circle cx="6" cy="5" r="2" /><circle cx="6" cy="19" r="2" /><circle cx="18" cy="19" r="2" /><path d="M6 7v10m8-14 4 4-4 4m4-4v10" /></>,
    usage: <path d="M5 20v-6m7 6V4m7 16V9" />,
    device: <><rect x="5" y="2" width="14" height="20" rx="2" /><path d="M10 18h4" /></>,
    linked_prs: <><path d="m9 15 6-6m-8 3-2 2a4 4 0 0 0 6 6l2-2m-2-12 2-2a4 4 0 0 1 6 6l-2 2" /></>,
    research: <><path d="M4 5h7v15H4zm7 0h9v15h-9M7 9h1m7 0h2m-2 4h2" /></>,
    activity: <path d="M2 12h5l3-8 4 16 3-8h5" />,
  };
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{paths[kind] || paths.file}</svg>;
}
