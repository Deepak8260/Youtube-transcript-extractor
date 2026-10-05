import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI calls /api on its own origin and Vite forwards it to
// FastAPI, so no CORS setup is needed. Override the target with API_PROXY.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
