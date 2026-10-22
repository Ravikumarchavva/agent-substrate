import { existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
let packageRoot = dirname(require.resolve("monaco-editor"));
while (!existsSync(resolve(packageRoot, "package.json"))) {
  const parent = dirname(packageRoot);
  if (parent === packageRoot)
    throw new Error("Cannot locate Monaco source package");
  packageRoot = parent;
}
const source = resolve(packageRoot, "esm/vs");
rmSync("public/monaco", { recursive: true, force: true });
mkdirSync("public/monaco", { recursive: true });
// Monaco's prebuilt assets embed an older sanitizer. Build its maintained ESM
// sources and resolve that one vendored module to our patched, locked dependency.
await build({
  stdin: {
    contents: `export * from ${JSON.stringify(resolve(source, "editor/editor.api.js"))};\nimport ${JSON.stringify(resolve(source, "languages/definitions/sql/register.js"))};`,
    resolveDir: process.cwd(),
  },
  bundle: true,
  format: "iife",
  globalName: "SubstrateMonaco",
  outfile: "public/monaco/editor.js",
  minify: true,
  loader: { ".ttf": "file" },
  plugins: [
    {
      name: "patched-sanitizer",
      setup(builder) {
        builder.onResolve({ filter: /dompurify\/dompurify\.js$/ }, () => ({
          path: require.resolve("dompurify"),
        }));
      },
    },
  ],
});
await build({
  entryPoints: [resolve(source, "editor/editor.worker.js")],
  bundle: true,
  format: "iife",
  outfile: "public/monaco/editor.worker.js",
  minify: true,
});
