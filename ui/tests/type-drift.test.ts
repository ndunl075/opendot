import { expect, it } from "vitest";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

it("check:types detects a changed generated file", async () => {
  // Exercise the real check script against a private copy. A timeout or stopped
  // test runner must never leave generated application sources modified.
  const temporary = await mkdtemp(path.join(os.tmpdir(), "opendot-drift-test-"));
  const scripts = path.join(temporary, "scripts");
  const expected = path.join(temporary, "src", "api");
  try {
    await mkdir(scripts, { recursive: true });
    await mkdir(expected, { recursive: true });
    await copyFile("scripts/check-types.mjs", path.join(scripts, "check-types.mjs"));
    // Importing the real generator keeps its contract/dependency resolution.
    await writeFile(path.join(scripts, "generate-types.mjs"), `import ${JSON.stringify(pathToFileURL(path.resolve("scripts/generate-types.mjs")).href)};`);
    await copyFile("src/api/endpoints.gen.ts", path.join(expected, "endpoints.gen.ts"));
    await writeFile(path.join(expected, "types.gen.ts"), `${await readFile("src/api/types.gen.ts", "utf8")}\n// drift\n`);
    const result = spawnSync(process.execPath, [path.join(scripts, "check-types.mjs")], { cwd: path.resolve("."), encoding: "utf8", timeout: 12000 });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Generated API types drifted: types.gen.ts");
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}, 15000);
