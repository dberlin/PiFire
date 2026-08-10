import { mkdir, readFile, readdir, rename, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { compileFromFile } from "json-schema-to-typescript";

const SCHEMA_DIRECTORY = "schema/contracts";
const TYPESCRIPT_DIRECTORY = "src/helpers/contracts";
const MANIFEST_PATH = join(SCHEMA_DIRECTORY, "manifest.json");

const COMPILER_OPTIONS = {
  additionalProperties: false,
  bannerComment:
    "/* eslint-disable */\n// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types",
  declareExternallyReferenced: true,
  strictIndexSignatures: true,
  unknownAny: true,
  unreachableDefinitions: true,
} as const;

function lfTerminated(output: string): string {
  return `${output.replace(/\r\n?/g, "\n").replace(/\n*$/, "")}\n`;
}

async function readManifest(): Promise<Record<string, string>> {
  const parsed: unknown = JSON.parse(await readFile(MANIFEST_PATH, "utf8"));
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${MANIFEST_PATH} must contain a JSON object`);
  }

  const entries = Object.entries(parsed);
  for (const [schema, output] of entries) {
    if (
      typeof output !== "string" ||
      schema !== schema.split(/[\\/]/).at(-1) ||
      output !== output.split(/[\\/]/).at(-1) ||
      !schema.endsWith(".schema.json") ||
      !output.endsWith(".gen.ts")
    ) {
      throw new Error(`Invalid web contract manifest entry: ${schema} -> ${String(output)}`);
    }
  }
  entries.sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
  return Object.fromEntries(entries) as Record<string, string>;
}

async function atomicWrite(path: string, content: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const temporaryPath = `${path}.${process.pid}.tmp`;
  try {
    await writeFile(temporaryPath, content, "utf8");
    await rename(temporaryPath, path);
  } finally {
    await unlink(temporaryPath).catch(() => undefined);
  }
}

async function filesBelow(directory: string): Promise<Set<string>> {
  const files = new Set<string>();

  async function visit(currentDirectory: string): Promise<void> {
    const entries = await readdir(currentDirectory, { withFileTypes: true }).catch(
      (error: NodeJS.ErrnoException) => {
        if (error.code === "ENOENT") return [];
        throw error;
      },
    );
    for (const entry of entries) {
      const path = join(currentDirectory, entry.name);
      if (entry.isFile()) files.add(path);
      else if (entry.isDirectory()) await visit(path);
    }
  }

  await visit(directory);
  return files;
}

export async function emitWebContracts(check: boolean): Promise<boolean> {
  const manifest = await readManifest();
  const expectedPaths = new Set(Object.values(manifest).map((output) => join(TYPESCRIPT_DIRECTORY, output)));
  let stale = false;

  for (const [schema, output] of Object.entries(manifest)) {
    const schemaPath = join(SCHEMA_DIRECTORY, schema);
    const outputPath = join(TYPESCRIPT_DIRECTORY, output);
    const generated = lfTerminated(await compileFromFile(schemaPath, COMPILER_OPTIONS));

    if (!check) {
      await atomicWrite(outputPath, generated);
      console.log(`Wrote ${outputPath}`);
      continue;
    }

    const committed = await readFile(outputPath, "utf8").catch((error: NodeJS.ErrnoException) => {
      if (error.code === "ENOENT") return null;
      throw error;
    });
    if (committed === null) {
      console.error(`missing: ${outputPath}`);
      stale = true;
    } else if (committed !== generated) {
      console.error(`changed: ${outputPath}`);
      stale = true;
    }
  }

  const unexpectedPaths = [...(await filesBelow(TYPESCRIPT_DIRECTORY))]
    .filter((path) => !expectedPaths.has(path))
    .sort();
  for (const unexpectedPath of unexpectedPaths) {
    if (check) {
      console.error(`unexpected: ${unexpectedPath}`);
      stale = true;
    } else {
      await unlink(unexpectedPath);
      console.log(`Removed ${unexpectedPath}`);
    }
  }

  if (check && !stale) console.log("Generated web contract TypeScript is up to date.");
  return !stale;
}

if (import.meta.path === Bun.main) {
  const check = process.argv.includes("--check");
  emitWebContracts(check).then(
    (upToDate) => {
      if (!upToDate) process.exitCode = 1;
    },
    (error) => {
      console.error(error);
      process.exitCode = 1;
    },
  );
}
