// Reminders that fire (apps/backend/agent/reminders.py) post into a chat and queue a notification
// (agent/notifications.py). The desktop shell shows those as Windows notifications itself, even
// from the tray; this feed lets the open window refresh the chat it landed in, and in a plain
// browser shows a browser notification instead.

export type FeedItem = { seq: number; title: string; body: string; session_id: string; kind: string };

/** What to do with one poll: the newest sequence seen, chats to refresh, and items to notify about. */
export function applyFeed(
  latestSeen: number,
  data: { items?: FeedItem[]; latest?: number },
  activeSessionId: string,
  desktop: boolean,
): { latest: number; refresh: string[]; notify: FeedItem[] } {
  const latest = Number(data?.latest ?? latestSeen);
  // The first poll only learns where the feed is, so old items don't pop up when the window opens.
  if (latestSeen < 0) return { latest, refresh: [], notify: [] };
  const items = (Array.isArray(data?.items) ? data.items : []).filter((item) => item.seq > latestSeen);
  const refresh = [...new Set(items.filter((i) => i.session_id && i.session_id === activeSessionId).map((i) => i.session_id))];
  return { latest: Math.max(latest, latestSeen), refresh, notify: desktop ? [] : items };
}

/** A browser notification, when the browser allows them (asks once). */
export async function browserNotify(title: string, body: string): Promise<void> {
  if (typeof Notification === "undefined") return;
  try {
    if (Notification.permission === "default") await Notification.requestPermission();
    if (Notification.permission === "granted") new Notification(title, { body });
  } catch {
    // Notifications are a convenience; the reminder is already in its chat.
  }
}
