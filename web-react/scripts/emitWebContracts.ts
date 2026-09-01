import { mkdir, readFile, readdir, rename, unlink, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compileFromFile } from "json-schema-to-typescript";
import ts from "typescript";

const SCHEMA_DIRECTORY = "schema/contracts";
// The generated TypeScript is consumed by every client, so the exporter (both
// this emitter and the Pydantic side in common/web_contracts/export.py)
// writes it into the shared @pifire/core package, a sibling of web-react —
// while the JSON schemas that drive it stay under web-react/schema, since no
// client reads raw schemas.
const TYPESCRIPT_DIRECTORY = "../packages/pifire-core/src/contracts";
const MANIFEST_PATH = join(SCHEMA_DIRECTORY, "manifest.json");
const WEB_REACT_ROOT = fileURLToPath(new URL("..", import.meta.url));
const OXFMT_EXECUTABLE = join(WEB_REACT_ROOT, "node_modules/.bin/oxfmt");
const OXFMT_CONFIG = join(WEB_REACT_ROOT, ".oxfmtrc.jsonc");

const COMPILER_OPTIONS = {
  additionalProperties: false,
  bannerComment:
    "/* oxlint-disable */\n// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types",
  declareExternallyReferenced: true,
  strictIndexSignatures: true,
  unknownAny: true,
  unreachableDefinitions: true,
} as const;

const TYPESCRIPT_EXPORTS_KEY = "x-pifire-typescript-exports";

// json-schema-to-typescript 15.0.4 mis-emits recursive array aliases as
// `Value | undefined[]` when strict index signatures are enabled. Keep strict
// dictionaries everywhere else; these schemas retain valid recursive aliases.
const RECURSIVE_SCHEMA_NAMES: Record<string, true> = {
  "control.schema.json": true,
  "wizard.schema.json": true,
};

function lfTerminated(output: string): string {
  return `${output.replace(/\r\n?/g, "\n").replace(/\n*$/, "")}\n`;
}

async function ownedTypeScriptExports(schemaPath: string): Promise<Set<string>> {
  const schema = JSON.parse(await readFile(schemaPath, "utf8")) as Record<string, unknown>;
  const configured = schema[TYPESCRIPT_EXPORTS_KEY];
  if (
    !Array.isArray(configured) ||
    configured.some((name) => typeof name !== "string" || !/^[A-Za-z_$][\w$]*$/.test(name))
  ) {
    throw new Error(`${schemaPath} must contain a valid ${TYPESCRIPT_EXPORTS_KEY} array`);
  }
  return new Set(configured);
}

function exposeOwnedDeclarations(generated: string, owned: ReadonlySet<string>): string {
  const sourceFile = ts.createSourceFile(
    "contracts.gen.ts",
    generated,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const declarations = new Map<
    string,
    ts.InterfaceDeclaration | ts.TypeAliasDeclaration
  >();
  for (const statement of sourceFile.statements) {
    if (ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)) {
      declarations.set(statement.name.text, statement);
    }
  }

  const roots = new Set(
    [...declarations.keys()].filter((name) => /^PiFire[A-Za-z]+WebContracts$/.test(name)),
  );
  const required = new Set([...owned, ...roots]);
  const pending = [...required];
  while (pending.length > 0) {
    const declaration = declarations.get(pending.pop()!);
    if (!declaration) continue;
    const visit = (node: ts.Node) => {
      if (ts.isTypeReferenceNode(node) && ts.isIdentifier(node.typeName)) {
        const dependency = node.typeName.text;
        if (declarations.has(dependency) && !required.has(dependency)) {
          required.add(dependency);
          pending.push(dependency);
        }
      } else if (ts.isExpressionWithTypeArguments(node) && ts.isIdentifier(node.expression)) {
        const dependency = node.expression.text;
        if (declarations.has(dependency) && !required.has(dependency)) {
          required.add(dependency);
          pending.push(dependency);
        }
      }
      ts.forEachChild(node, visit);
    };
    ts.forEachChild(declaration, visit);
  }

  const bannerEnd = generated.indexOf("\n\n") + 2;
  const sections = [generated.slice(0, bannerEnd)];
  for (const statement of sourceFile.statements) {
    if (
      (ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)) &&
      !required.has(statement.name.text)
    ) {
      continue;
    }
    const start = Math.max(statement.getFullStart(), bannerEnd);
    let section = generated.slice(start, statement.end);
    if (
      (ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)) &&
      !owned.has(statement.name.text) &&
      !roots.has(statement.name.text)
    ) {
      section = section.replace(/^export (?=(?:interface|type) )/m, "");
    }
    sections.push(section);
  }
  return `${sections.join("").replace(/\n*$/, "")}\n`;
}

function resolveManifestPath(base: string, allowedRoot: string, path: string): string {
  const destination = resolve(base, path);
  const fromAllowedRoot = relative(resolve(allowedRoot), destination);
  if (
    fromAllowedRoot === "" ||
    fromAllowedRoot === ".." ||
    fromAllowedRoot.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) ||
    isAbsolute(fromAllowedRoot)
  ) {
    throw new Error(`Manifest path escapes ${allowedRoot}: ${path}`);
  }
  return destination;
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
      !schema.endsWith(".schema.json") ||
      !output.endsWith(".gen.ts")
    ) {
      throw new Error(`Invalid web contract manifest entry: ${schema} -> ${String(output)}`);
    }
    resolveManifestPath(SCHEMA_DIRECTORY, "schema", schema);
    resolveManifestPath(TYPESCRIPT_DIRECTORY, "../packages/pifire-core/src", output);
  }
  entries.sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
  return Object.fromEntries(entries) as Record<string, string>;
}

async function formatGeneratedTypeScript(generated: string, outputPath: string): Promise<string> {
  // The formatter only honors this config's indent settings for a
  // --stdin-filepath it can place under the config's own root; an absolute
  // path pointing outside web-react (as outputPath does, since the contracts
  // moved into the sibling @pifire/core package) falls back to built-in
  // defaults. A WEB_REACT_ROOT-relative path — the same "../packages/..."
  // shape TYPESCRIPT_DIRECTORY already uses — keeps it resolving the config
  // correctly.
  const formatterStdinPath = relative(WEB_REACT_ROOT, outputPath);
  const formatter = Bun.spawn(
    [OXFMT_EXECUTABLE, "-c", OXFMT_CONFIG, "--stdin-filepath", formatterStdinPath],
    {
      cwd: WEB_REACT_ROOT,
      stdin: new Blob([generated]),
      stdout: "pipe",
      stderr: "pipe",
    },
  );
  const formattedOutput = new Response(formatter.stdout).text();
  const formatterError = new Response(formatter.stderr).text();
  const exitCode = await formatter.exited;
  if (exitCode !== 0) {
    throw new Error(`oxfmt failed to format ${outputPath}: ${await formatterError}`);
  }
  return lfTerminated(await formattedOutput);
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
  const expectedPaths = new Set(
    Object.values(manifest).map((output) =>
      resolveManifestPath(TYPESCRIPT_DIRECTORY, "../packages/pifire-core/src", output),
    ),
  );
  let stale = false;

  for (const [schema, output] of Object.entries(manifest)) {
    const schemaPath = resolveManifestPath(SCHEMA_DIRECTORY, "schema", schema);
    const outputPath = resolveManifestPath(TYPESCRIPT_DIRECTORY, "../packages/pifire-core/src", output);
    const compilerOptions = RECURSIVE_SCHEMA_NAMES[schema]
      ? { ...COMPILER_OPTIONS, strictIndexSignatures: false }
      : COMPILER_OPTIONS;
    const compiled = await compileFromFile(schemaPath, compilerOptions);
    const generated = await formatGeneratedTypeScript(
      exposeOwnedDeclarations(compiled, await ownedTypeScriptExports(schemaPath)),
      outputPath,
    );

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
    .map((path) => resolve(path))
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
