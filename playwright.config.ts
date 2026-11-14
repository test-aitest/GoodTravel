import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const port = 3100;
const dataDir = (process.env.CLIENTOPS_E2E_DATA_DIR ??= mkdtempSync(join(tmpdir(), "clientops-e2e-")));

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.pw.ts",
  fullyParallel: true,
  forbidOnly: true,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${port}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome" } }],
  webServer: [
    {
      command: `bun run build && bun run start --port ${port}`,
      url: `http://localhost:${port}`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: { CLIENTOPS_EXTERNALS: "fake", CLIENTOPS_JOBS: "playback", CLIENTOPS_DATA_DIR: dataDir },
    },
    {
      command: "bun sandbox/sites/server.ts",
      url: "http://127.0.0.1:3001/login",
      reuseExistingServer: false,
      timeout: 10_000,
    },
  ],
});
