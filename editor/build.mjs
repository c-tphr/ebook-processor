// Builds the note editor into one file the app serves: ebook_processor/web/static/editor.js
//   npm ci && npm run build
// The versions come from package-lock.json, so the same source always gives the same file.
import { build } from "esbuild";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const lock = JSON.parse(readFileSync(here("./package-lock.json"), "utf8"));
const result = await build({
  entryPoints: [here("./src/index.js")], bundle: true, minify: true, format: "iife", target: ["es2020"],
  legalComments: "none", metafile: true, write: false, outfile: "editor.js",
});
const out = Object.values(result.metafile.outputs)[0];
const size = {};
for (const [path, part] of Object.entries(out.inputs)) {
  const name = (path.match(/node_modules\/((?:@[^/]+\/)?[^/]+)/) || [null, "editor/src"])[1];
  if (part.bytesInOutput) size[name] = (size[name] || 0) + part.bytesInOutput;
}
const packages = Object.keys(size).filter((name) => name !== "editor/src").sort()
  .map((name) => `${name} ${lock.packages["node_modules/" + name].version}`);
const banner = `/* The note editor of ebook-processor. Built from editor/src/index.js with \`npm run build\`; do not edit by hand.
 * It contains these packages, all under the MIT licence (the texts are in editor/THIRD-PARTY.md):
 * ${packages.join(", ")}
 */
`;
writeFileSync(here("../ebook_processor/web/static/editor.js"), banner + result.outputFiles[0].text);
console.log(`editor.js: ${((banner.length + out.bytes) / 1024).toFixed(0)} KB`);
for (const [name, bytes] of Object.entries(size).sort((a, b) => b[1] - a[1])) console.log(`  ${(bytes / 1024).toFixed(0).padStart(4)} KB  ${name}`);
