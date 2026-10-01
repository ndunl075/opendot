// Generate the app icons from the UI's own mark (drawn by Astra in the design system, ui/public/app-icon.svg),
// so the desktop app and the web UI share one identity. `tauri icon` writes src-tauri/icons/.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(desktop, "..", "ui", "public", "app-icon.svg");
if (!existsSync(source)) {
  console.error("ui/public/app-icon.svg is missing (the app mark comes from the UI design system).");
  process.exit(1);
}
execFileSync("pnpm", ["exec", "tauri", "icon", source], {
  cwd: desktop,
  stdio: "inherit",
  shell: process.platform === "win32",
});
