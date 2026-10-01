// Build the daemon as one executable with PyInstaller, named the way Tauri expects a sidecar:
// src-tauri/binaries/opendot-daemon-<rust target triple>[.exe]. The built UI ships inside it too,
// so `opendot serve` from the sidecar serves the web UI without a separate checkout.
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const desktop = resolve(here, "..");
const repo = resolve(desktop, "..");
const daemon = join(repo, "daemon");
const uiDist = join(repo, "ui", "dist");
const work = join(desktop, ".sidecar-build");
const isWindows = process.platform === "win32";

if (!existsSync(join(uiDist, "index.html"))) {
  console.error("ui/dist is missing: run `pnpm -C ui build` first (`pnpm -C desktop build` does).");
  process.exit(1);
}

const triple = execFileSync("rustc", ["-vV"], { encoding: "utf8" })
  .split("\n")
  .find((line) => line.startsWith("host:"))
  ?.slice("host:".length)
  .trim();
if (!triple) {
  console.error("could not read the Rust host target triple from `rustc -vV`");
  process.exit(1);
}

rmSync(work, { recursive: true, force: true });
mkdirSync(work, { recursive: true });

const args = [
  "run", "--project", daemon, "pyinstaller",
  "--noconfirm", "--clean", "--onefile",
  "--name", "opendot-daemon",
  "--distpath", join(work, "dist"),
  "--workpath", join(work, "build"),
  "--specpath", work,
  "--collect-all", "opendot_core",
  "--collect-all", "sqlite_vec",
  "--collect-submodules", "keyring",
  "--collect-submodules", "uvicorn",
  "--add-data", `${uiDist}${delimiter === ";" ? ";" : ":"}ui`,
  join(daemon, "packaging", "opendot_daemon.py"),
];
console.log(`building sidecar for ${triple} ...`);
execFileSync("uv", args, { stdio: "inherit", cwd: repo, shell: isWindows });

const built = join(work, "dist", isWindows ? "opendot-daemon.exe" : "opendot-daemon");
const binaries = join(desktop, "src-tauri", "binaries");
mkdirSync(binaries, { recursive: true });
const target = join(binaries, `opendot-daemon-${triple}${isWindows ? ".exe" : ""}`);
copyFileSync(built, target);

// Smoke test: the frozen daemon must open a database (migrations, the sqlite-vec extension) and
// build the serving app. --help alone would miss native modules PyInstaller failed to collect.
const smokeDb = join(work, "smoke.db");
execFileSync(target, ["--db", smokeDb, "init"], { stdio: "inherit" });
console.log(`sidecar ready: ${target}`);
