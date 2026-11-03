import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// The SPA is served by FastAPI, at the site root (dashboard at http://host:PORT/)
// OR under /s/<slug>/ when the box is reached through its reverse proxy on port
// 80 (core/services/webaccess.py). Build output lands in ../core/static/app:
// index.html + a content-hashed assets/ dir. master_server.py / hub_server.py
// mount /assets from there and a catch-all returns index.html for every client
// route, so URLs stay clean (/platform, /manage/<id>, /login) and hashed assets
// cache hard.
//
// base "./": the built paths are RELATIVE, and the server writes the page's
// own root into <base href> ("/" or "/s/<slug>/", core/services/weblinks.py),
// so one bundle serves both. Chunks load relative to the module that asks.
//
// Dev loop: `npm run dev` runs Vite on :5173 and proxies the API + auth + media
// paths to a locally running master (default :5110) so the React app talks to
// real endpoints with hot reload.
//
// Two BUILDS, not two apps:
//
//   npm run build            owner's build -- includes the developer panels
//   npm run build:customer   the shipped appliance -- excludes them
//
// `__DEV_TOOLS__` is substituted as a literal true/false at build time, so
// `__DEV_TOOLS__ ? lazy(() => import("./TemplatesPanel.jsx")) : null` collapses
// to `null` in a customer build and Rollup never emits the chunk. The panels
// are ABSENT from the bundle, not hidden by a check a curious user can flip in
// devtools -- matching the backend, where the same build has no core/dev/ and
// the routes those panels call do not exist (core/deploy.py).
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  base: "./",
  define: {
    __DEV_TOOLS__: JSON.stringify(mode !== "customer"),
  },
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  build: {
    outDir: "../core/static/app",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ["/api", "/video_feed", "/static", "/system-static"].map((p) => [
        p,
        { target: process.env.MASTER_URL || "http://127.0.0.1:5110", changeOrigin: true },
      ])
    ),
  },
}));
