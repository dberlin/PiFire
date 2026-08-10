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


function exportedDeclarationNames(source: string, filename: string): string[] {
  const kind = filename.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, kind);
  return sourceFile.statements.flatMap((statement) => {
    if (!ts.isInterfaceDeclaration(statement) && !ts.isTypeAliasDeclaration(statement)) return [];
    const exported = statement.modifiers?.some(
      (modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword,
    );
    return exported ? [statement.name.text] : [];
  });
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

function memberNames(members: ts.NodeArray<ts.TypeElement>): Set<string> {
  const names = new Set<string>();
  for (const member of members) {
    if (!ts.isPropertySignature(member) || member.name === undefined) continue;
    if (ts.isIdentifier(member.name) || ts.isStringLiteral(member.name)) names.add(member.name.text);
  }
  return names;
}

function isEnvelopeMembers(members: ts.NodeArray<ts.TypeElement>): boolean {
  const names = memberNames(members);
  return names.has("result") && names.has("data");
}

function containsJsonCall(node: ts.Node): boolean {
  let found = false;
  const visit = (child: ts.Node) => {
    if (
      ts.isCallExpression(child) &&
      ts.isPropertyAccessExpression(child.expression) &&
      child.expression.name.text === "json"
    ) {
      found = true;
      return;
    }
    ts.forEachChild(child, visit);
  };
  visit(node);
  return found;
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
  const residuals = new Set<string>();
  for (const filename of filesBelow(root)) {
    const source = readFileSync(filename, "utf8");
    const kind = filename.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sourceFile = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, kind);
    const localDeclarations = new Set<string>();
    for (const statement of sourceFile.statements) {
      if (ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)) {
        const name = statement.name.text;
        localDeclarations.add(name);
        if (ownedNames.has(name) || LEGACY_MIRROR_NAMES[name]) {
          residuals.add(`${relative(root, filename)}:${name}`);
        }
      }
    }

    const addNamed = (name: string) => residuals.add(`${relative(root, filename)}:${name}`);
    const inspectWireType = (type: ts.TypeNode, expression: ts.Expression) => {
      if (!containsJsonCall(expression)) return;
      if (ts.isTypeLiteralNode(type) && isEnvelopeMembers(type.members)) {
        residuals.add(`${relative(root, filename)}:<inline-envelope>`);
      } else if (
        ts.isTypeReferenceNode(type) &&
        ts.isIdentifier(type.typeName) &&
        localDeclarations.has(type.typeName.text) &&
        (ownedNames.has(type.typeName.text) || LEGACY_MIRROR_NAMES[type.typeName.text])
      ) {
        addNamed(type.typeName.text);
      }
    };

    const visit = (node: ts.Node) => {
      if (ts.isInterfaceDeclaration(node)) {
        if (LEGACY_MIRROR_NAMES[node.name.text] || isEnvelopeMembers(node.members)) {
          addNamed(node.name.text);
        }
      } else if (ts.isTypeAliasDeclaration(node)) {
        if (
          LEGACY_MIRROR_NAMES[node.name.text] ||
          (ts.isTypeLiteralNode(node.type) && isEnvelopeMembers(node.type.members))
        ) {
          addNamed(node.name.text);
        }
      } else if (ts.isAsExpression(node)) {
        inspectWireType(node.type, node.expression);
      } else if (ts.isVariableDeclaration(node) && node.type && node.initializer) {
        inspectWireType(node.type, node.initializer);
      }
      ts.forEachChild(node, visit);
    };
    visit(sourceFile);
  }
  return [...residuals].sort();
}
describe("generated web contract ownership", () => {
  it("reports a renamed envelope mirror", () => {
    const root = mkdtempSync(join(tmpdir(), "pifire-contract-inventory-"));
    try {
      writeFileSync(
        join(root, "renamed.ts"),
        "interface TransportBody { result?: string; message?: string; data?: unknown }",
      );
      expect(residualMirrors(root, pythonOwnedNames())).toEqual(["renamed.ts:TransportBody"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("reports an inline envelope mirror at JSON consumption", () => {
    const root = mkdtempSync(join(tmpdir(), "pifire-contract-inventory-"));
    try {
      writeFileSync(
        join(root, "inline.ts"),
        "async function read(response: Response) { return (await response.json()) as { result?: string; message?: string; data?: unknown }; }",
      );
      expect(residualMirrors(root, pythonOwnedNames())).toEqual(["inline.ts:<inline-envelope>"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("allows a frontend-local view type", () => {
    const root = mkdtempSync(join(tmpdir(), "pifire-contract-inventory-"));
    try {
      writeFileSync(join(root, "local.ts"), "interface AdminPanelState { selected: boolean }");
      expect(residualMirrors(root, pythonOwnedNames())).toEqual([]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("exports each generated contract declaration from exactly one manifest artifact", () => {
    const owners = new Map<string, string[]>();
    for (const artifact of generatedArtifacts()) {
      const names = exportedDeclarationNames(
        readFileSync(artifact.typescript, "utf8"),
        artifact.typescript,
      );
      for (const name of names) {
        const paths = owners.get(name) ?? [];
        paths.push(relative(WEB_ROOT, artifact.typescript));
        owners.set(name, paths);
      }
    }
    const duplicates = [...owners]
      .filter(([, paths]) => paths.length > 1)
      .map(([name, paths]) => `${name}: ${paths.join(", ")}`)
      .sort();
    expect(duplicates).toEqual([]);
  });

  it("keeps migrated helpers free of Python-owned interface and type declarations", () => {
    const residuals = residualMirrors(HELPERS_ROOT, pythonOwnedNames());
    if (residuals.length > 0) throw new Error(`Python-owned contract mirrors:\n${residuals.join("\n")}`);
  });

});
