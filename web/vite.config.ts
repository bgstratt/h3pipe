/// <reference types="vitest/config" />
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two builds from one config:
//  - `vite build`: the ComfyUI extension. ONE ES module, CSS inlined into the JS,
//    written to comfy_nodes/web/ (the node pack's WEB_DIRECTORY). ComfyUI's own
//    app/api come in at runtime from /scripts/*.js, so they stay external.
//  - `vite` / `vite build --mode devpage`: dev.html, every surface side by side
//    on the mock API (src/mock/). Never part of the extension bundle.

const HERE = dirname(fileURLToPath(import.meta.url));

// Bare ids in the source (typed in src/comfy-env.d.ts), rewritten to the
// paths ComfyUI serves, relative to /extensions/<pack>/h3pipe-editor.js.
const COMFY_EXTERNALS: Record<string, string> = {
  "comfyui/app": "../../scripts/app.js",
  "comfyui/api": "../../scripts/api.js",
};

export default defineConfig(({ command, mode }) => {
  const extension = command === "build" && mode !== "devpage";
  return {
    plugins: [react()],
    define: extension ? { "process.env.NODE_ENV": JSON.stringify("production") } : {},
    // the mock reads the kitchen_sink series config from tests/fixtures (outside web/)
    server: { open: "/dev.html", fs: { allow: [resolve(HERE, "..")] } },
    build: extension
      ? {
          outDir: resolve(HERE, "../comfy_nodes/web"),
          emptyOutDir: false, // other files (e.g. .gitkeep) live there too
          target: "es2022",
          minify: true,
          sourcemap: false,
          copyPublicDir: false,
          lib: {
            entry: resolve(HERE, "src/main.ts"),
            formats: ["es"],
            fileName: () => "h3pipe-editor.js",
          },
          rollupOptions: {
            external: Object.keys(COMFY_EXTERNALS),
            // one file: ComfyUI loads every .js in WEB_DIRECTORY as an extension.
            // Lib "es" mode keeps whitespace by default; minify it fully.
            output: { paths: COMFY_EXTERNALS, codeSplitting: false, minify: true },
          },
        }
      : {
          outDir: resolve(HERE, "dist-dev"),
          chunkSizeWarningLimit: 2000, // the mock fixtures are ~1 MB of real episode data
          rollupOptions: { input: resolve(HERE, "dev.html") },
        },
    test: {
      include: ["test/**/*.test.ts"],
      environment: "node",
    },
  };
});
