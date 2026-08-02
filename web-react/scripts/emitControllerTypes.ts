// Per-controller config types, from the same manifest the backend reads.
// `settings.controller.config` is a loose dict server-side on purpose -- a
// controller can be added by dropping a file in controller/ -- so this narrows
// only what the client indexes into.

export interface ControllerOptionDecl {
  option_name: string;
  option_type: string;
  list_values?: (string | number)[];
}

export interface ControllerManifest {
  metadata: Record<string, { config?: ControllerOptionDecl[] }>;
}

const BANNER =
  "/* eslint-disable */\n" +
  "// GENERATED from controller/controllers.json — do not edit. Regenerate: bun run gen:types";

/** `pid_parallel` -> `PidParallel`. */
function pascal(name: string): string {
  return name
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}

function tsType(option: ControllerOptionDecl): string {
  switch (option.option_type) {
    case "float":
    case "int":
      return "number";
    case "bool":
      return "boolean";
    case "list":
      // A declared value set is the whole point of a list option; falling back
      // to `string` would accept a value the controller rejects at runtime.
      return option.list_values?.length
        ? option.list_values.map((v) => JSON.stringify(v)).join(" | ")
        : "string";
    default:
      return "string";
  }
}

export function emitControllerTypes(manifest: ControllerManifest): string {
  const names = Object.keys(manifest.metadata);
  const blocks: string[] = [];

  for (const name of names) {
    const options = manifest.metadata[name].config ?? [];
    const iface = `${pascal(name)}Config`;
    if (options.length === 0) {
      blocks.push(`export type ${iface} = Record<string, never>;`);
      continue;
    }
    const fields = options
      .map((option) => `  ${option.option_name}: ${tsType(option)};`)
      .join("\n");
    blocks.push(`export interface ${iface} {\n${fields}\n}`);
  }

  const members = names.map((name) => `  ${name}: ${pascal(name)}Config;`).join("\n");
  blocks.push(`export interface ControllerConfigs {\n${members}\n}`);

  return `${BANNER}\n\n${blocks.join("\n\n")}\n`;
}
