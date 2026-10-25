import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 端口纪律 R6：webui dev 固定 8090；API 全部代理到 Leader 8041（含后续只读增强端点）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8090,
    strictPort: true,
    proxy: {
      "/task": "http://127.0.0.1:8041",
      "/companion": "http://127.0.0.1:8041",
      "/stats": "http://127.0.0.1:8041",
      "/amp": "http://127.0.0.1:8041",
      "/identity": "http://127.0.0.1:8041",
      "/protocol": "http://127.0.0.1:8041",
      "/health": "http://127.0.0.1:8041",
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom"],
          "vendor-marked": ["marked"],
        },
      },
    },
  },
});
