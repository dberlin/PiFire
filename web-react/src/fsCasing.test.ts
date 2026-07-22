import { readdirSync, statSync } from "node:fs";
import { join, parse } from "node:path";
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
