import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:5173" },
  webServer: [
    { command: "uv run --project ../daemon opendot mock-server", port: 8787, reuseExistingServer: !process.env.CI },
    { command: "corepack pnpm dev -- --host 127.0.0.1", port: 5173, reuseExistingServer: !process.env.CI }
  ]
});
