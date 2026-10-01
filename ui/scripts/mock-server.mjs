import { access, readdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const uiRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = process.platform === "win32";
const executableNames = isWindows ? ["uv.exe", "uv"] : ["uv"];

async function exists(candidate) {
  try {
    await access(candidate);
    return true;
  } catch {
    return false;
  }
}

async function findUv() {
  if (process.env.UV) return process.env.UV;

  for (const directory of (process.env.PATH ?? "").split(path.delimiter)) {
    for (const name of executableNames) {
      const candidate = path.join(directory, name);
      if (await exists(candidate)) return candidate;
    }
  }

  const home = process.env.USERPROFILE ?? process.env.HOME;
  const candidates = [];
  if (process.env.APPDATA) {
    try {
      const entries = await readdir(path.join(process.env.APPDATA, "Python"), { withFileTypes: true });
      for (const entry of entries.filter((entry) => entry.isDirectory() && /^Python3/.test(entry.name))) {
        candidates.push(path.join(process.env.APPDATA, "Python", entry.name, "Scripts", "uv.exe"));
      }
    } catch {
      // This common Windows install location is optional.
    }
  }
  if (home) {
    for (const directory of [path.join(home, ".local", "bin"), path.join(home, ".cargo", "bin")]) {
      for (const name of executableNames) candidates.push(path.join(directory, name));
    }
  }
  for (const candidate of candidates) {
    if (await exists(candidate)) return candidate;
  }
  return undefined;
}

const uv = await findUv();
if (!uv) {
  console.error("Unable to find uv. Set UV to its executable path or install uv so it is available on PATH.");
  process.exitCode = 1;
} else {
  const child = spawn(uv, ["run", "--project", "../daemon", "python", path.join(uiRoot, "scripts", "mock_app.py")], {
    cwd: uiRoot,
    stdio: "inherit"
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => child.kill(signal));
  }
  child.on("error", (error) => {
    console.error(`Failed to start uv at ${uv}: ${error.message}`);
    process.exitCode = 1;
  });
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exitCode = code ?? 1;
  });
}
