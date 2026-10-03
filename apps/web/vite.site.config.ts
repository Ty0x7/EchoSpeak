import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

/**
 * The public website only (front page + docs), for GitHub Pages.
 * SITE_BASE is the path the site is served from: "/EchoSpeak/" on GitHub Pages,
 * "/" once it has its own domain (or on Vercel).
 */
export default defineConfig({
  root: fileURLToPath(new URL("./site", import.meta.url)),
  base: process.env.SITE_BASE || "/EchoSpeak/",
  publicDir: fileURLToPath(new URL("./public", import.meta.url)),
  plugins: [react()],
  define: {
    "import.meta.env.VITE_APP_VERSION": JSON.stringify(version),
  },
  build: {
    outDir: fileURLToPath(new URL("./dist-site", import.meta.url)),
    emptyOutDir: true,
  },
  server: { port: 5175 },
});
