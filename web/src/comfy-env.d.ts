// ComfyUI's frontend modules, loaded at runtime from /scripts/app.js and
// /scripts/api.js (see COMFY_EXTERNALS in vite.config.ts). Only comfy.ts
// imports them. Typed loosely: comfy.ts narrows what it uses.

declare module "comfyui/app" {
  export const app: unknown;
}

declare module "comfyui/api" {
  export const api: unknown;
}
