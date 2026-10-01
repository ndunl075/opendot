import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { environment: "jsdom", setupFiles: ["./tests/setup.ts"], exclude: ["e2e/**", "node_modules/**", "dist/**"] }
});
