import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, normalize, parse, relative, resolve } from "node:path";
import { describe, expect, it } from "@rstest/core";

// Guard against case-insensitive-filesystem breakage (macOS/Windows): two
// modules in one directory whose names differ only in case (or only in
// extension after case-folding, e.g. controlButtons.ts vs ControlButtons.tsx)
// make `import "./ControlButtons"` resolve to DIFFERENT files on Linux vs
// macOS — TS extension priority (.ts before .tsx) silently picks the wrong
// one where the filesystem folds case. Linux CI never sees the error, so
// this test is the only cross-platform tripwire.
function collectCollisions(dir: string, found: string[]): void {
  const entries = readdirSync(dir);
  const stems = new Map<string, string[]>();
  for (const entry of entries) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      collectCollisions(full, found);
      continue;
    }
    const stem = parse(entry).name.toLowerCase();
    const bucket = stems.get(stem) ?? [];
    bucket.push(entry);
    stems.set(stem, bucket);
  }
  for (const [, names] of stems) {
    // Same-stem pairs are only safe when they are a module + its own
    // aux files of non-importable kinds (e.g. Foo.tsx + Foo.css). Any
    // pair of two IMPORTABLE extensions (.ts/.tsx/.js/.jsx) sharing a
    // case-folded stem is a resolution ambiguity.
    const importable = names.filter(
      (n) => /\.(ts|tsx|js|jsx)$/.test(n) && !/\.(test|spec)\./.test(n),
    );
    if (importable.length > 1) {
      found.push(`${dir}: ${importable.join(" vs ")}`);
    }
  }
}

describe("filesystem casing", () => {
  it("has no case-folded module-name collisions in src/", () => {
    const found: string[] = [];
    collectCollisions("src", found);
    expect(found).toEqual([]);
  });
});

// Guard against layering violations: helpers/ (pure logic, API clients,
// hooks) must never import from components/ (React component modules).
// components/ -> helpers/ is the only allowed direction; root infra
// (main.tsx etc.) may import both. This is a lightweight line-regex scan
// of import specifiers, not a real module resolver, but it's enough to
// catch the two ways a helper could reach into components/: an absolute
// "components/..." style specifier, or a relative "../" specifier that
// resolves into src/components.
function collectFiles(dir: string, found: string[]): void {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      collectFiles(full, found);
      continue;
    }
    if (/\.tsx?$/.test(entry)) {
      found.push(full);
    }
  }
}

function importSpecifiers(file: string): string[] {
  const specifiers: string[] = [];
  const text = readFileSync(file, "utf8");
  const re = /from\s+["']([^"']+)["']/g;
  let match: RegExpExecArray | null = re.exec(text);
  while (match !== null) {
    specifiers.push(match[1]);
    match = re.exec(text);
  }
  return specifiers;
}

function resolvesIntoComponents(file: string, specifier: string): boolean {
  if (specifier.includes("components/")) {
    return true;
  }
  if (!specifier.startsWith(".")) {
    return false;
  }
  const resolved = normalize(resolve(dirname(file), specifier));
  const rel = relative(resolve("src/components"), resolved);
  return rel === "" || !rel.startsWith("..");
}

describe("component/helper layering", () => {
  it("helpers/ never imports from components/", () => {
    const files: string[] = [];
    collectFiles("src/helpers", files);
    const violations: string[] = [];
    for (const file of files) {
      for (const specifier of importSpecifiers(file)) {
        if (resolvesIntoComponents(file, specifier)) {
          violations.push(`${file}: imports "${specifier}"`);
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
