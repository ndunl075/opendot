import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const contractDir = path.resolve(root, "..", "contract");
const outputDir = process.env.OPENDOT_TYPES_OUT ?? path.join(root, "src", "api");
const header = "/* This file is generated from contract/. Do not edit. */\n\n";

const schemas = (await readdir(path.join(contractDir, "schemas")))
  .filter((name) => name.endsWith(".json"))
  .sort();
const generated = [];
for (const filename of schemas) {
  const schema = JSON.parse(await readFile(path.join(contractDir, "schemas", filename), "utf8"));
  const name = path.basename(filename, ".json");
  const compiled = (await compile(schema, name, {
    bannerComment: "",
    style: { singleQuote: true }
  })).trim();
  generated.push(`export namespace ${name}Schema {\n${compiled.split("\n").map((line) => line ? `  ${line}` : "").join("\n")}\n}\nexport type ${name} = ${name}Schema.${name};`);
}

const index = JSON.parse(await readFile(path.join(contractDir, "index.json"), "utf8"));
const endpointRows = [...index.endpoints]
  .sort((a, b) => a.name.localeCompare(b.name))
  .map((endpoint) => `  ${JSON.stringify(endpoint.name)}: { method: ${JSON.stringify(endpoint.method)}; path: ${JSON.stringify(endpoint.path)}; request: ${endpoint.request ?? "never"}; response: ${endpoint.response}; requestIn: ${endpoint.request_in ? JSON.stringify(endpoint.request_in) : "null"} };`)
  .join("\n");
const endpointValues = [...index.endpoints]
  .sort((a, b) => a.name.localeCompare(b.name))
  .map((endpoint) => `  ${JSON.stringify(endpoint.name)}: { method: ${JSON.stringify(endpoint.method)}, path: ${JSON.stringify(endpoint.path)}, requestIn: ${endpoint.request_in ? JSON.stringify(endpoint.request_in) : "null"} },`)
  .join("\n");
const streamEvents = index.stream.server_events
  .map((event) => event.model)
  .sort()
  .join(" | ");
const clientFrames = index.stream.client_frames
  .map((frame) => frame.model)
  .sort()
  .join(" | ");
const importedTypes = [...new Set([
  ...index.endpoints.flatMap((endpoint) => [endpoint.request, endpoint.response]),
  ...index.stream.server_events.map((event) => event.model),
  ...index.stream.client_frames.map((frame) => frame.model)
].filter(Boolean))].sort().join(", ");
const endpoints = `${header}import type { ${importedTypes} } from "./types.gen";\n\nexport interface EndpointTable {\n${endpointRows}\n}\n\nexport const endpointTable = {\n${endpointValues}\n} as const;\n\nexport type EndpointName = keyof EndpointTable;\nexport type StreamEvent = ${streamEvents};\nexport type ClientFrame = ${clientFrames};\nexport const streamPath = ${JSON.stringify(index.stream.path)} as const;\n`;

await mkdir(outputDir, { recursive: true });
await writeFile(path.join(outputDir, "types.gen.ts"), `${header}${generated.join("\n\n")}\n`);
await writeFile(path.join(outputDir, "endpoints.gen.ts"), endpoints);
