import { useEffect, useState } from "react";

/**
 * Appearance: light (the default since 11.1), dark, or whatever the system uses.
 *
 * The choice is a per-device preference kept in localStorage. Every EchoSpeak
 * window (main, Settings, setup, companion) shares that storage, and the
 * `storage` event tells the other windows the moment it changes, so they all
 * switch together. Applied before React renders (main.tsx) to avoid a flash.
 */
export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const KEY = "echospeak.theme";
const CHANGE = "echospeak:theme";

export function readThemeChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "dark" || value === "system" || value === "light" ? value : "light";
  } catch {
    return "light";
  }
}

const systemPrefersDark = (): boolean =>
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;

export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice === "system") return systemPrefersDark() ? "dark" : "light";
  return choice;
}

export function applyTheme(choice: ThemeChoice = readThemeChoice()): ResolvedTheme {
  const theme = resolveTheme(choice);
  const root = document.documentElement;
  root.dataset.esTheme = theme;
  root.dataset.esThemeChoice = choice;
  return theme;
}

export function setThemeChoice(choice: ThemeChoice): void {
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    // Storage unavailable: the theme still applies to this window.
  }
  applyTheme(choice);
  window.dispatchEvent(new CustomEvent(CHANGE, { detail: choice }));
}

/** Keep this window in step with the choice made in any window, and with the system theme. */
export function installThemeSync(): () => void {
  applyTheme();
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEY) applyTheme();
  };
  const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const onSystem = () => {
    if (readThemeChoice() === "system") applyTheme("system");
  };
  window.addEventListener("storage", onStorage);
  media?.addEventListener?.("change", onSystem);
  return () => {
    window.removeEventListener("storage", onStorage);
    media?.removeEventListener?.("change", onSystem);
  };
}

/** The current choice and the theme it resolves to, updating with every change. */
export function useTheme(): { choice: ThemeChoice; theme: ResolvedTheme; setChoice: (choice: ThemeChoice) => void } {
  const [choice, setChoice] = useState<ThemeChoice>(readThemeChoice);
  const [theme, setTheme] = useState<ResolvedTheme>(() => resolveTheme(readThemeChoice()));
  useEffect(() => {
    const refresh = () => {
      const next = readThemeChoice();
      setChoice(next);
      setTheme(resolveTheme(next));
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === KEY) refresh();
    };
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    window.addEventListener(CHANGE, refresh);
    window.addEventListener("storage", onStorage);
    media?.addEventListener?.("change", refresh);
    return () => {
      window.removeEventListener(CHANGE, refresh);
      window.removeEventListener("storage", onStorage);
      media?.removeEventListener?.("change", refresh);
    };
  }, []);
  return { choice, theme, setChoice: setThemeChoice };
}
