import { expect, it } from "vitest";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";

it("check:types detects a changed generated file", async () => {
  const target = path.resolve("src/api/types.gen.ts");
  await writeFile(target, `${await readFile(target, "utf8")}\n// drift\n`);
  try {
    const result = spawnSync(process.execPath, ["scripts/check-types.mjs"], { cwd: path.resolve("."), encoding: "utf8" });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Generated API types drifted");
  } finally {
    await writeFile(target, (await readFile(target, "utf8")).replace("\n// drift\n", ""));
  }
});
