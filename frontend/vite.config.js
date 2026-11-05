// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
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
export default defineConfig(() => ({
  plugins: [react(), tailwindcss()],
  base: "./",
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
