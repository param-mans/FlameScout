import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { vexcelImagery } from "./vexcel-imagery.mjs";

// /api and /ws proxy to the FastAPI backend (default :8000) once it exists.
// vexcelImagery() answers /api/house-viewer/vexcel/* itself (before the proxy), with the key from the repo .env.
export default defineConfig({
  plugins: [react(), tailwindcss(), vexcelImagery()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
