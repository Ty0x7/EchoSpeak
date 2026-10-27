import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { adaptXtermForFrozenPrototype } from "./tooling/xtermCompatibility.mjs";

const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

export default defineConfig({
  plugins: [{
    name: "xterm-frozen-prototype-compatibility",
    enforce: "pre",
    transform(source, id) {
      if (!id.replace(/\\/g, "/").endsWith("/@xterm/xterm/lib/xterm.mjs")) return null;
      return { code: adaptXtermForFrozenPrototype(source), map: null };
    },
  }, react()],
  // Apply the same compatibility transform in development, rather than caching
  // an untransformed optimized dependency with a different startup behavior.
  optimizeDeps: { exclude: ["@xterm/xterm"] },
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
