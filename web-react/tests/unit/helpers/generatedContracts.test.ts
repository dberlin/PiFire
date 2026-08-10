import { describe, expect, it } from "@rstest/core";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const WEB_ROOT = fileURLToPath(new URL("../../..", import.meta.url));
const HELPERS_ROOT = join(WEB_ROOT, "src/helpers");
const MANIFEST_PATH = join(WEB_ROOT, "schema/contracts/manifest.json");
const LEGACY_MIRROR_NAMES: Record<string, true> = {
  AdminEnvelope: true,
  LogsMetadataEnvelope: true,
  SystemCmd: true,
  TunerEnvelope: true,
  UpdateEnvelope: true,
};

function declarationNames(source: string, filename: string): string[] {
  const kind = filename.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, kind);
  const names: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)) names.push(node.name.text);
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return names;
}

function filesBelow(root: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) files.push(...filesBelow(path));
    else if ([".ts", ".tsx"].includes(extname(entry.name)) && !entry.name.endsWith(".gen.ts")) {
      files.push(path);
    }
  }
  return files;
}

function generatedArtifacts(): Array<{ schema: string; typescript: string }> {
  const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8")) as Record<string, string>;
  const schemaRoot = join(WEB_ROOT, "schema/contracts");
  const typescriptRoot = join(WEB_ROOT, "src/helpers/contracts");
  return Object.entries(manifest).map(([schema, typescript]) => ({
    schema: resolve(schemaRoot, schema),
    typescript: resolve(typescriptRoot, typescript),
  }));
}

function pythonOwnedNames(): Set<string> {
  const names = new Set<string>();
  for (const artifact of generatedArtifacts()) {
    const schema = JSON.parse(readFileSync(artifact.schema, "utf8")) as {
      $defs?: Record<string, unknown>;
      title?: string;
    };
    for (const name of Object.keys(schema.$defs ?? {})) names.add(name);
    if (schema.title && /^[A-Za-z_$][\w$]*$/.test(schema.title)) names.add(schema.title);
  }
  return names;
}

function residualMirrors(root: string, ownedNames: ReadonlySet<string>): string[] {
  const residuals: string[] = [];
  for (const filename of filesBelow(root)) {
    const source = readFileSync(filename, "utf8");
    for (const name of declarationNames(source, filename)) {
      if (ownedNames.has(name) || LEGACY_MIRROR_NAMES[name]) {
        residuals.push(`${relative(root, filename)}:${name}`);
      }
    }
  }
  return residuals.sort();
}

describe("generated web contract ownership", () => {
  it("reports a representative injected Python-owned mirror through the TypeScript AST", () => {
    const root = mkdtempSync(join(tmpdir(), "pifire-contract-inventory-"));
    try {
      writeFileSync(join(root, "residual.ts"), "export interface AdminState { mode: string }");
      expect(residualMirrors(root, pythonOwnedNames())).toEqual(["residual.ts:AdminState"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("keeps migrated helpers free of Python-owned interface and type declarations", () => {
    const residuals = residualMirrors(HELPERS_ROOT, pythonOwnedNames());
    if (residuals.length > 0) throw new Error(`Python-owned contract mirrors:\n${residuals.join("\n")}`);
  });

});
