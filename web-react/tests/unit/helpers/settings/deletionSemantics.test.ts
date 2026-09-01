import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "@rstest/core";

import { SETTINGS_DELTA_KEY, settingsDelta } from "../../../../src/helpers/settings/settingsDelta";

type Tree = Record<string, unknown>;

function isPlainObject(value: unknown): value is Tree {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function deepMerge(base: Tree, partial: Tree): Tree {
  const merged: Tree = { ...base };
  for (const [key, value] of Object.entries(partial)) {
    const existing = merged[key];
    merged[key] =
      isPlainObject(existing) && isPlainObject(value) ? deepMerge(existing, value) : value;
  }
  return merged;
}

function deleteAtPath(tree: Tree, path: string[]): Tree {
  const [head, ...rest] = path;
  if (head === undefined) return tree;
  if (rest.length === 0) {
    const remainder: Tree = { ...tree };
    delete remainder[head];
    return remainder;
  }
  const child = tree[head];
  if (!isPlainObject(child)) return tree;
  return { ...tree, [head]: deleteAtPath(child, rest) };
}

// Mirrors apply_settings_delta's documented contract (common/settings_schema.py):
// a bare partial deep-merges into the tree; a settingsDelta() envelope's `set`
// deep-merges the same way, and its `delete` paths are removed afterward. This
// exists to make that consequence checkable from the frontend side, not to
// reimplement or validate the backend itself.
function applyToPersistedTree(tree: Tree, payload: object): Tree {
  const envelope = payload as { set?: Tree; delete?: string[][] };
  const isDelta = SETTINGS_DELTA_KEY in payload;
  let merged = deepMerge(tree, isDelta ? (envelope.set ?? {}) : (payload as Tree));
  if (isDelta) {
    for (const path of envelope.delete ?? []) merged = deleteAtPath(merged, path);
  }
  return merged;
}

describe("deletion semantics", () => {
  // Every case below merges against the same persisted tree: two OneSignal
  // devices already on record.
  const persisted: Tree = {
    notify_services: {
      onesignal: {
        devices: {
          "player-a": { friendly_name: "Kitchen" },
          "player-b": { friendly_name: "Garage" },
        },
      },
    },
  };

  it("a plain body that omits a key does not remove it", () => {
    // player-b is simply absent -- exactly what a component sends if it drops
    // a key from its own in-memory object before calling save() with a plain
    // body instead of a settingsDelta().
    const plainBody: Tree = {
      notify_services: {
        onesignal: { devices: { "player-a": { friendly_name: "Kitchen" } } },
      },
    };

    const result = applyToPersistedTree(persisted, plainBody);

    const devices = (
      (result.notify_services as Tree).onesignal as { devices: Record<string, unknown> }
    ).devices;
    expect(devices["player-b"]).toEqual({ friendly_name: "Garage" });
  });

  it("a settingsDelta naming the key in `delete` removes it", () => {
    const delta = settingsDelta(
      { notify_services: { onesignal: { devices: { "player-a": { friendly_name: "Kitchen" } } } } },
      [["notify_services", "onesignal", "devices", "player-b"]],
    );

    const result = applyToPersistedTree(persisted, delta);

    const devices = (
      (result.notify_services as Tree).onesignal as { devices: Record<string, unknown> }
    ).devices;
    expect(devices["player-b"]).toBeUndefined();
    expect(devices["player-a"]).toEqual({ friendly_name: "Kitchen" });
  });
});

// A file-level check, not a registry: a registry only protects the surfaces
// someone remembered to list in it, and a NEW surface that forgets the
// delete channel never enrols itself. The fact below is adjacent in the
// source, unlike the `integer`-prop scanner this plan rejected elsewhere for
// correlating things across JSX distance -- this needs nothing but the file
// itself: if it deletes a key from an indexed member, it must import
// settingsDelta.
const SCANNED_ROOTS = ["src/components/settings", "src/components/wizard"];

// `delete <ident>[...]`, optionally through a dotted path (`delete a.b[c]`).
// Requires whitespace then an identifier then `[` right after the `delete`
// keyword, so an identifier that merely starts with the letters "delete"
// (`deleteDevice`), a handler prop (`onDelete`), and a type member
// (`delete?: string[][]`) don't match -- none of them delete anything. Proven
// against literal snippets and the real tree below.
const DELETES_AN_INDEXED_KEY = /\bdelete\s+[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\[/;

const IMPORTS_SETTINGS_DELTA =
  /import\s*\{[^}]*\bsettingsDelta\b[^}]*\}\s*from\s*["'][^"']*\/settingsDelta["']/;

function listSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...listSourceFiles(full));
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

// Every file that deletes an indexed key without importing settingsDelta
// must be named here, with why. Asserted below for exact equality against
// what the scan actually finds, so an entry that stops applying -- the file
// starts importing settingsDelta, or stops deleting a key -- is caught as a
// stale allowlist entry rather than silently left in place. Empty today:
// nothing is exempt.
const ALLOWLIST: Record<string, string> = {};

describe("DELETES_AN_INDEXED_KEY matches only an indexed-member delete", () => {
  it("matches an indexed delete, including through a dotted path", () => {
    expect(DELETES_AN_INDEXED_KEY.test("delete devices[deviceId];")).toBe(true);
    expect(DELETES_AN_INDEXED_KEY.test("for (const key of unknownKeys) delete rebuilt[key];")).toBe(
      true,
    );
    expect(DELETES_AN_INDEXED_KEY.test("delete s.ns[name];")).toBe(true);
  });

  it("does not match a deleteX identifier, an onDelete prop, or a delete type member", () => {
    expect(DELETES_AN_INDEXED_KEY.test("const deleteDevice = (deviceId: string) =>")).toBe(false);
    expect(DELETES_AN_INDEXED_KEY.test('onDelete={() => remove(id)} aria-label="Delete"')).toBe(
      false,
    );
    expect(DELETES_AN_INDEXED_KEY.test("delete?: string[][];")).toBe(false);
    expect(DELETES_AN_INDEXED_KEY.test("delete: string[][];")).toBe(false);
  });
});

describe("every component that deletes an indexed key imports settingsDelta", () => {
  const sourceByFile = new Map<string, string>();
  for (const root of SCANNED_ROOTS) {
    for (const file of listSourceFiles(resolve(root))) {
      sourceByFile.set(file, readFileSync(file, "utf8"));
    }
  }

  const wouldBeOffenders = [...sourceByFile.entries()]
    .filter(([, source]) => DELETES_AN_INDEXED_KEY.test(source))
    .filter(([, source]) => !IMPORTS_SETTINGS_DELTA.test(source))
    .map(([file]) => relative(resolve("."), file))
    .sort();

  it("the allowlist names exactly the files that delete an indexed key without importing settingsDelta", () => {
    expect(wouldBeOffenders).toEqual(Object.keys(ALLOWLIST).sort());
  });
});
