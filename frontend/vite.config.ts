import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server runs on 5173 (the origin the backend's CORS policy allows).
// The backend base URL is supplied to the app via VITE_API_BASE_URL, so no
// production address is hardcoded in components.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
});
