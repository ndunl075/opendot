// Build the Tauri app and its installer (after the UI, the sidecar and the icons).
import { tauri } from "./run.mjs";

tauri([process.argv[2] === "dev" ? "dev" : "build"]);
