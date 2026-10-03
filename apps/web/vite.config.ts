import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

export default defineConfig({
  plugins: [react()],
  define: {
    // The website's download section shows the version being built.
    "import.meta.env.VITE_APP_VERSION": JSON.stringify(version),
  },
  resolve: {
    extensions: [".tsx", ".ts", ".jsx", ".js", ".json"],
  },
  server: {
    port: 5174,
  },
});
