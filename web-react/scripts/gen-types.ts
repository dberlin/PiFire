// Wrapper around json-schema-to-typescript's library API (compileFromFile),
// used instead of a raw `json2ts ...` package.json script line because the
// multi-line --bannerComment string fights package.json/bun script quoting.
//
// Usage:
//   bun scripts/gen-types.ts          -> writes each generated artifact
//   bun scripts/gen-types.ts --check  -> compares each artifact's generated
//                                        output against the committed file
import { compileFromFile } from "json-schema-to-typescript";
import { readFile, writeFile } from "node:fs/promises";
import { emitControllerTypes } from "./emitControllerTypes";
import { emitSettingsDefaults } from "./emitSettingsDefaults";

const SCHEMA_PATH = "schema/settings.schema.json";
const CONTROLLERS_PATH = "../controller/controllers.json";
const BANNER_COMMENT =
  "/* eslint-disable */\n" +
  "// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types";

interface Artifact {
  out: string;
  generate: () => Promise<string>;
}

const ARTIFACTS: Artifact[] = [
  {
    out: "src/helpers/settings/settingsTypes.gen.ts",
    generate: () => compileFromFile(SCHEMA_PATH, { bannerComment: BANNER_COMMENT }),
  },
  {
    out: "src/helpers/settings/controllerTypes.gen.ts",
    generate: async () =>
      emitControllerTypes(JSON.parse(await readFile(CONTROLLERS_PATH, "utf8"))),
  },
  {
    out: "src/helpers/settings/settingsDefaults.gen.ts",
    generate: async () =>
      emitSettingsDefaults(JSON.parse(await readFile(SCHEMA_PATH, "utf8"))),
  },
];

async function main() {
  const check = process.argv.includes("--check");
  let stale = false;

  for (const artifact of ARTIFACTS) {
    const output = await artifact.generate();
    if (!check) {
      await writeFile(artifact.out, output);
      console.log(`Wrote ${artifact.out}`);
      continue;
    }
    const committed = await readFile(artifact.out, "utf8").catch(() => null);
    if (committed === null) {
      console.error(`${artifact.out} does not exist — run 'bun run gen:types' first.`);
      stale = true;
    } else if (committed !== output) {
      console.error(`${artifact.out} is out of date. Run 'bun run gen:types' to regenerate.`);
      stale = true;
    } else {
      console.log(`${artifact.out} is up to date.`);
    }
  }

  if (stale) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
