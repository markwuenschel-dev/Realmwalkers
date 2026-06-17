import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev server proxies nothing; the client talks to the API at VITE_API_BASE (default :8000)
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
