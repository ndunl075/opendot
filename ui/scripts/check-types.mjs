import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const expected = path.join(root, "src", "api");
const temporary = await mkdtemp(path.join(os.tmpdir(), "opendot-types-"));
const child = spawn(process.execPath, [path.join(root, "scripts", "generate-types.mjs")], {
  env: { ...process.env, OPENDOT_TYPES_OUT: temporary },
  stdio: "inherit"
});
const exitCode = await new Promise((resolve) => child.on("exit", resolve));
if (exitCode !== 0) process.exit(exitCode ?? 1);
try {
  for (const name of ["types.gen.ts", "endpoints.gen.ts"]) {
    if (await readFile(path.join(expected, name), "utf8") !== await readFile(path.join(temporary, name), "utf8")) {
      console.error(`Generated API types drifted: ${name}. Run pnpm gen:types and commit the result.`);
      process.exitCode = 1;
      break;
    }
  }
} finally {
  await rm(temporary, { recursive: true, force: true });
}
