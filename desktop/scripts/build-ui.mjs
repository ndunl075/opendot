// Build the UI that the desktop app bundles (and that the sidecar serves on the web).
import { join } from "node:path";
import { pnpm, repo } from "./run.mjs";

const ui = join(repo, "ui");
pnpm(["-C", ui, "install", "--frozen-lockfile"]);
pnpm(["-C", ui, "build"]);
