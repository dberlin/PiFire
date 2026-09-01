// Wrapper around json-schema-to-typescript's library API (compileFromFile),
// used instead of a raw `json2ts ...` package.json script line because the
// multi-line --bannerComment string fights package.json/bun script quoting.
//
// Usage:
//   bun scripts/gen-types.ts          -> writes each generated artifact
//   bun scripts/gen-types.ts --check  -> compares each artifact's generated
//                                        output against the committed file
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { emitSettingsDefaults } from "./emitSettingsDefaults";
import { emitWebContracts } from "./emitWebContracts";

const SCHEMA_PATH = "schema/settings.schema.json";
const REPOSITORY_ROOT = fileURLToPath(new URL("../..", import.meta.url));
interface Artifact {
  out: string;
  generate: () => Promise<string>;
}

const ARTIFACTS: Artifact[] = [
  {
    out: "src/helpers/settings/settingsDefaults.gen.ts",
    generate: async () =>
      emitSettingsDefaults(JSON.parse(await readFile(SCHEMA_PATH, "utf8"))),
  },
];

async function exportPydanticSchemas(check: boolean): Promise<void> {
  const child = Bun.spawn(
    ["uv", "run", "--no-sync", "python", "-m", "common.web_contracts.export", check ? "--check" : "--write"],
    {
      cwd: REPOSITORY_ROOT,
      stdout: "inherit",
      stderr: "inherit",
    },
  );
  const exitCode = await child.exited;
  if (exitCode !== 0) {
    throw new Error(`Pydantic web contract exporter exited with status ${exitCode}`);
  }
}

async function main() {
  const check = process.argv.includes("--check");
  let stale = false;
  await exportPydanticSchemas(check);

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

  if (!(await emitWebContracts(check))) stale = true;

  if (stale) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
