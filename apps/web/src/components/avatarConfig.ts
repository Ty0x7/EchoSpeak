export type AvatarConfig = {
  body_color: string;
  eye_color: string;
  bg_color: string;
  glow_color: string;
  idle_activity: string;
  breathing_speed: number;
  eye_size: number;
  body_roundness: number;
  enable_glow: boolean;
  enable_idle_activities: boolean;
  custom_status_text: string;
  voice_avatar_scale: number;
};

export const DEFAULT_AVATAR_CONFIG: AvatarConfig = {
  body_color: "#ffffff",
  eye_color: "#000000",
  bg_color: "#0a0a0a",
  glow_color: "#ffffff",
  idle_activity: "auto",
  breathing_speed: 1,
  eye_size: 1,
  body_roundness: 14,
  enable_glow: true,
  enable_idle_activities: true,
  custom_status_text: "",
  voice_avatar_scale: 1,
};

export function normalizeAvatarConfig(input?: Partial<AvatarConfig> | null): AvatarConfig {
  const result = { ...DEFAULT_AVATAR_CONFIG, ...input };
  for (const key of ["body_color", "eye_color", "bg_color", "glow_color"] as const) {
    const value = String(result[key] || "");
    result[key] = /^#[0-9a-f]{3}$/i.test(value) ? `#${value.slice(1).split("").map(c => c + c).join("")}`
      : /^#[0-9a-f]{6}$/i.test(value) ? value : DEFAULT_AVATAR_CONFIG[key];
  }
  for (const [key, min, max] of [["breathing_speed", .4, 2.6], ["eye_size", .5, 2], ["body_roundness", 4, 40], ["voice_avatar_scale", .7, 1.2]] as const) {
    const value = Number(result[key]);
    result[key] = Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : DEFAULT_AVATAR_CONFIG[key];
  }
  result.custom_status_text = String(result.custom_status_text || "").slice(0, 120);
  return result;
}
