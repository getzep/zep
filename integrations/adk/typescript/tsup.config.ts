import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  target: "es2022",
  dts: true,
  sourcemap: true,
  clean: true,
  treeshake: true,
  // Peer / external deps are resolved by the consumer, not bundled.
  external: ["@google/adk", "@google/genai", "@getzep/zep-cloud"],
});
