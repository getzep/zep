import { defineConfig } from "vite";

export default defineConfig({
  server: {
    open: "/index.html",
  },
  build: {
    outDir: "../static",
  },
  root: "src",
});
