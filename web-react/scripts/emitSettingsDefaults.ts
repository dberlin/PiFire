// The settings tree's static defaults, as the schema already resolves them.
// Each top-level section carries a fully nested `default`; the three that do not
// (versions, server_info, lastupdated) are generated per install and have no
// static value to publish.

const BANNER =
  "/* eslint-disable */\n" +
  "// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types";

interface SchemaLike {
  // The index signature keeps sections that carry only a `$ref` (no `default`)
  // structurally assignable -- otherwise TypeScript's weak-type check rejects
  // them for sharing no property with an all-optional `{ default?: unknown }`.
  properties: Record<string, { default?: unknown; [key: string]: unknown }>;
}

export function emitSettingsDefaults(schema: SchemaLike): string {
  const defaults: Record<string, unknown> = {};
  for (const [section, node] of Object.entries(schema.properties)) {
    if (node.default === undefined) continue;
    defaults[section] = node.default;
  }
  const body = JSON.stringify(defaults, null, 2);
  return `${BANNER}\n\nexport const SETTINGS_DEFAULTS = ${body} as const;\n`;
}
