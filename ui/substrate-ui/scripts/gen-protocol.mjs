/**
 * Generate src/protocol/protocol.gen.ts from the engine's exported JSON Schema.
 *
 * Run the engine export first (writes protocol.schema.json):
 *   cd ../ravi-engine && make protocol-schema
 * Then:
 *   pnpm gen:protocol
 *
 * Reads the schema, generates TypeScript interfaces for every wire event and
 * request body, and writes a single protocol.gen.ts. Keeps the engine Pydantic
 * models as the single source of truth — never hand-edit protocol.gen.ts.
 */
import { compile } from "json-schema-to-typescript";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const SCHEMA_PATH = resolve(
  __dirname,
  "../../ravi-engine/src/ravi/serving/protocol/protocol.schema.json"
);
const OUT_PATH = resolve(__dirname, "../src/protocol/protocol.gen.ts");

if (!existsSync(SCHEMA_PATH)) {
  console.error(
    `\nSchema not found at ${SCHEMA_PATH}\n` +
      `Run the engine export first:  cd ../ravi-engine && make protocol-schema\n`
  );
  process.exit(1);
}

const schema = JSON.parse(readFileSync(SCHEMA_PATH, "utf8"));
const version = schema["x-protocol-version"] ?? "unknown";

const ts = await compile(schema, "RaviProtocol", {
  bannerComment:
    `/* eslint-disable */\n` +
    `/**\n * GENERATED — DO NOT EDIT.\n` +
    ` * Source: ravi-engine/src/ravi/serving/protocol/ (Pydantic).\n` +
    ` * Regenerate: cd ../ravi-engine && make protocol-schema && cd ../ravi-ui && pnpm gen:protocol\n` +
    ` * Protocol version: ${version}\n */\n`,
  additionalProperties: false,
  declareExternallyReferenced: true,
  enableConstEnums: false,
});

// Append a runtime version constant + a convenience WireEvent union alias.
const footer =
  `\nexport const GENERATED_PROTOCOL_VERSION = ${JSON.stringify(version)};\n`;

writeFileSync(OUT_PATH, ts + footer);
console.log(`Wrote ${OUT_PATH} (protocol v${version})`);
