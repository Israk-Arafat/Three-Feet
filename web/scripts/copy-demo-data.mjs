// Adds the recorded demo (web/demo-data) to the static build. Run after `vite build --mode demo`.
import { cpSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(root, "demo-data");
if (!existsSync(resolve(source, "recording.json"))) {
  console.error("web/demo-data/recording.json is missing. Run: python scripts/record_demo.py");
  process.exit(1);
}
cpSync(source, resolve(root, "dist", "demo"), { recursive: true });
console.log("Copied the recorded demo into dist/demo");
