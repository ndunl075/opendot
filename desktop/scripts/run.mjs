// Helpers for the desktop build scripts. Package scripts must not call `pnpm` by name: on some
// Windows machines pnpm is on the user's shell PATH but not on the PATH cmd.exe sees. pnpm puts
// its own entry point in npm_execpath, so nested pnpm calls go through Node instead.
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const repo = resolve(desktop, "..");

export function pnpm(args, options = {}) {
  const entry = process.env.npm_execpath;
  if (!entry) throw new Error("run this through pnpm (pnpm -C desktop <script>)");
  execFileSync(process.execPath, [entry, ...args], { stdio: "inherit", ...options });
}

export function tauri(args, options = {}) {
  const cli = resolve(desktop, "node_modules", "@tauri-apps", "cli", "tauri.js");
  execFileSync(process.execPath, [cli, ...args], { stdio: "inherit", cwd: desktop, ...options });
}

/** uv: the UV variable, then PATH, then the usual per-user install locations. */
export function findUv() {
  const exe = process.platform === "win32" ? "uv.exe" : "uv";
  const candidates = [];
  if (process.env.UV) candidates.push(process.env.UV);
  for (const dir of (process.env.PATH ?? "").split(delimiter)) if (dir) candidates.push(join(dir, exe));
  const appData = process.env.APPDATA;
  if (appData && existsSync(join(appData, "Python"))) {
    for (const version of readdirSync(join(appData, "Python")).sort().reverse()) {
      candidates.push(join(appData, "Python", version, "Scripts", exe));
    }
  }
  candidates.push(join(homedir(), ".local", "bin", exe), join(homedir(), ".cargo", "bin", exe));
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) throw new Error("uv was not found: install it or set UV to its path");
  return found;
}
