import {defineConfig} from "vite";
import {fileURLToPath} from "node:url";

const apiOrigin = process.env.FLOORPLAN_API_URL || "http://127.0.0.1:8020";
const proxy = {"/api": apiOrigin};
export default defineConfig({
  server: {
    proxy,
    fs: {allow: [fileURLToPath(new URL(".", import.meta.url)),
                 fileURLToPath(new URL("../phase1_viewer_frontend", import.meta.url))]},
  },
  preview: {proxy},
});
