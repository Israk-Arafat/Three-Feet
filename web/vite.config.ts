import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = "http://127.0.0.1:8000";

// The Pages demo is served from a sub-path (/Three-Feet/), so it uses relative asset URLs.
export default defineConfig(({ mode }) => ({
  base: mode === "demo" ? "./" : "/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/stream.mjpg": backend,
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
}));
