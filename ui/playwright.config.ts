import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:5173" },
  webServer: [
    {
      command: "node scripts/mock-server.mjs",
      url: "http://127.0.0.1:8787/v1/health",
      timeout: 120_000,
      reuseExistingServer: !process.env.CI
    },
    {
      command: "node ./node_modules/vite/bin/vite.js",
      url: "http://127.0.0.1:5173/",
      timeout: 120_000,
      reuseExistingServer: !process.env.CI
    }
  ]
});
