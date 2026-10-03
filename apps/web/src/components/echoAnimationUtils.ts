// Tool name → visual category mapping for Echo's avatar animations

export type ToolCategory =
  | "search"
  | "discord_read"
  | "discord_post"
  | "file_read"
  | "file_write"
  | "browser"
  | "terminal"
  | "memory_store"
  | "memory_recall"
  | "generic";

export type EchoReaction = "success" | "error" | "memory_saved";

// Whether it's currently nighttime (for ambient dimming)
export function isNightTime(): boolean {
  const h = new Date().getHours();
  return h >= 22 || h < 6;
}
