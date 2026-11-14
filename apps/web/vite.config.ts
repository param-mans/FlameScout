import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { houseData } from "./server/house-data.mjs";

// /api and /ws proxy to the FastAPI backend (default :8000) once it exists.
// houseData() answers /api/house-viewer/* itself (before the proxy): live Matterport plans and Vexcel imagery,
// with the keys from the repo .env (server side only).
export default defineConfig({
  plugins: [react(), tailwindcss(), houseData()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
