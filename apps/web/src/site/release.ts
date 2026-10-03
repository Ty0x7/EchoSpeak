import { useEffect, useState } from "react";

export const GITHUB_URL = "https://github.com/Ty0x7/EchoSpeak";
export const RELEASES_URL = `${GITHUB_URL}/releases`;
const API = "https://api.github.com/repos/Ty0x7/EchoSpeak/releases/latest";

export type LatestRelease = {
  version: string;
  exeUrl: string;
  msiUrl: string;
  sizeMb: number;
  date: string;
  notesUrl: string;
};

/** Fallback while loading or if GitHub can't be reached: the releases page always has the newest installer. */
export const FALLBACK_RELEASE: LatestRelease = {
  version: String(import.meta.env.VITE_APP_VERSION || ""),
  exeUrl: `${RELEASES_URL}/latest`,
  msiUrl: `${RELEASES_URL}/latest`,
  sizeMb: 0,
  date: "",
  notesUrl: `${RELEASES_URL}/latest`,
};

let cached: Promise<LatestRelease> | null = null;

export function fetchLatestRelease(): Promise<LatestRelease> {
  cached ??= fetch(API, { headers: { Accept: "application/vnd.github+json" } })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((data) => {
      const assets: { name: string; browser_download_url: string; size: number }[] = data.assets || [];
      const exe = assets.find((a) => /setup\.exe$/i.test(a.name)) || assets.find((a) => /\.exe$/i.test(a.name));
      const msi = assets.find((a) => /\.msi$/i.test(a.name));
      return {
        version: String(data.tag_name || "").replace(/^v/, "") || FALLBACK_RELEASE.version,
        exeUrl: exe?.browser_download_url || FALLBACK_RELEASE.exeUrl,
        msiUrl: msi?.browser_download_url || FALLBACK_RELEASE.msiUrl,
        sizeMb: exe ? Math.round(exe.size / 1_000_000) : 0,
        date: data.published_at ? new Date(data.published_at).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "",
        notesUrl: data.html_url || FALLBACK_RELEASE.notesUrl,
      };
    })
    .catch(() => FALLBACK_RELEASE);
  return cached;
}

/** The newest GitHub release, so the download button always offers the latest installer. */
export function useLatestRelease(): LatestRelease {
  const [release, setRelease] = useState<LatestRelease>(FALLBACK_RELEASE);
  useEffect(() => {
    let live = true;
    void fetchLatestRelease().then((r) => live && setRelease(r));
    return () => {
      live = false;
    };
  }, []);
  return release;
}
