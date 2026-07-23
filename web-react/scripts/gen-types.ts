// Wrapper around json-schema-to-typescript's library API (compileFromFile),
// used instead of a raw `json2ts ...` package.json script line because the
// multi-line --bannerComment string fights package.json/bun script quoting.
//
// Usage:
//   bun scripts/gen-types.ts          -> writes src/helpers/settings/settingsTypes.gen.ts
//   bun scripts/gen-types.ts --check  -> compiles to a temp file and diffs it
//                                        against the committed file (drift check)
import { compileFromFile } from "json-schema-to-typescript";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SCHEMA_PATH = "schema/settings.schema.json";
const OUT_PATH = "src/helpers/settings/settingsTypes.gen.ts";
const BANNER_COMMENT =
  "/* eslint-disable */\n" +
  "// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types";

async function generate(): Promise<string> {
  return compileFromFile(SCHEMA_PATH, { bannerComment: BANNER_COMMENT });
}

async function main() {
  const check = process.argv.includes("--check");
  const output = await generate();

  if (!check) {
    await writeFile(OUT_PATH, output);
    console.log(`Wrote ${OUT_PATH}`);
    return;
  }

  const tmpDir = await mkdtemp(join(tmpdir(), "settingsTypes.gen.check-"));
  const tmpFile = join(tmpDir, "settingsTypes.gen.check.ts");
  try {
    await writeFile(tmpFile, output);
    const committed = await readFile(OUT_PATH, "utf8").catch(() => null);
    if (committed === null) {
      console.error(`${OUT_PATH} does not exist — run 'bun run gen:types' first.`);
      process.exit(1);
    }
    if (committed !== output) {
      console.error(
        `${OUT_PATH} is out of date with ${SCHEMA_PATH}. Run 'bun run gen:types' to regenerate.`,
      );
      process.exit(1);
    }
    console.log(`${OUT_PATH} is up to date.`);
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
