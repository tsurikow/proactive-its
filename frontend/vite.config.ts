import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const devHost = env.VITE_DEV_HOST || "127.0.0.1";
  const devPort = Number(env.VITE_DEV_PORT || "5173");
  const apiProxyTarget = env.VITE_DEV_API_PROXY_TARGET || "http://localhost:8000";
  const mediaProxyTarget = env.VITE_DEV_MEDIA_PROXY_TARGET || apiProxyTarget;

  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes("node_modules/react") || id.includes("node_modules/react-dom")) {
              return "react-vendor";
            }
            if (
              id.includes("react-markdown") ||
              id.includes("remark-gfm") ||
              id.includes("remark-math") ||
              id.includes("rehype-katex") ||
              id.includes("katex")
            ) {
              return "markdown-math";
            }
            if (id.includes("lucide-react")) {
              return "icons";
            }
            return undefined;
          },
        },
      },
    },
    server: {
      host: devHost,
      port: devPort,
      proxy: {
        "/v1": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        "/media": {
          target: mediaProxyTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
