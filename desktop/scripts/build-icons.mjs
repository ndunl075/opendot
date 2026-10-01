// Generate the app icons from the UI's own mark (drawn by Astra in the design system, ui/public/app-icon.svg),
// so the desktop app and the web UI share one identity. `tauri icon` writes src-tauri/icons/.
import { existsSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tauri } from "./run.mjs";

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(desktop, "..", "ui", "public", "app-icon.svg");
if (!existsSync(source)) {
  console.error("ui/public/app-icon.svg is missing (the app mark comes from the UI design system).");
  process.exit(1);
}
tauri(["icon", source]);
// Desktop only: drop the mobile icon sets `tauri icon` also writes.
for (const mobile of ["android", "ios"]) {
  rmSync(join(desktop, "src-tauri", "icons", mobile), { recursive: true, force: true });
}
