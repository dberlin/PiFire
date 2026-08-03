# Settings Toolchain Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven open *Schema and toolchain follow-ups* from
`backlogs/react-migration-backlog.md`, per
`docs/superpowers/specs/2026-08-02-settings-toolchain-followups-design.md`.

**Architecture:** Three independent slices. **A** turns two manifests
(`controller/controllers.json`, `web-react/schema/settings.schema.json`) into
generated TypeScript, then makes the settings tabs consume it and types the
deep-path writer. **B** stops the backend flattening its per-field validation
errors into one string and routes each one to the widget that caused it. **C**
adds observe-only validation on the read path and clears a four-item
accessibility/correctness backlog in the settings widgets.

**Tech Stack:** Python 3.14 + pydantic v2 (strict) + Flask; React 19 +
TypeScript 7 (via the `typescript7` alias) + rsbuild + Biome; rstest for unit
tests, pytest for the Python side; jj (Jujutsu) for version control.

## Global Constraints

Every task's requirements implicitly include this section.

- **Version control is jj, not git.** Run `jj new` **before** the first Write of
  a task. Commit with `jj describe --stdin < msgfile` — there is no `-F` flag,
  and a double-quoted `-m` with backticks is eaten by zsh. Never run a reflex
  `jj squash`: your edits are already in `@`, and squashing moves them into the
  parent.
- **Python tests:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <path> -q`.
  A bare `python`/`pytest` gives false failures — the venv holds PySide6.
- **Python format:** `.venv/bin/ruff format <files>` before every commit, and
  `.venv/bin/ruff check`. **Never `uvx ruff`** — the repo pins ruff <0.16.
  `except (A, B)` → `except A, B` is ruff-canonical here (Python 3.14+); do not
  "fix" it back.
- **web-react uses bun, never npm.** Tests are **rstest**: `bun run test`
  (never `bun test` — that is bun's own runner and fails with a vitest-style
  import error). Gates are `bun run typecheck`, `bun run lint` (Biome; must stay
  at **0 errors**, 2 pre-existing warnings are expected), `bun run build`.
- **Do not touch the live database or the running processes.** A `gunicorn`
  (`--reload`, :5000) and a guarded `control.py` are running against the real
  `pifire.db`. Do not start, stop, or drive either, and do not write to
  `pifire.db`.
- **Before running any script, grep its path** for `os.system`, `subprocess`,
  `sudo`, `reboot`, `shutdown` and neutralise what you find. A `real_hw=False`
  flag is not enough.
- **Source comments state what the code achieves** — never the change, the
  measurement, or the reasoning that produced it. No "previously…", no "this
  fixes…".
- **A cross-process seam needs a shape-pinning test at BOTH ends.** A payload
  field asserted only in a TypeScript fixture is not pinned; the producer needs
  its own test.
- **Enumerate references with serena's `find_referencing_symbols`, never grep.**
  A regex is line-oriented and literal: it misses a multi-line call and any
  argument passed as a variable. Writing this plan, grep gave a call-site
  breakdown for `setPath` that was wrong in three places — see Task 5. Use grep
  only as a cross-check on a name serena has already enumerated.
- **Baseline to beat:** Python `4707 passed, 4 skipped`; web-react
  `1752 passed`. Run the full suites before your first change if you need to
  confirm the baseline moved for a reason you caused.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `web-react/scripts/gen-types.ts` | modify | Becomes a three-emitter driver: settings types, controller config types, settings defaults. Each with a `--check` drift gate. |
| `web-react/scripts/emitControllerTypes.ts` | create | Pure `(manifest object) → string`. Reads nothing, writes nothing. |
| `web-react/scripts/emitSettingsDefaults.ts` | create | Pure `(schema object) → string`. Reads nothing, writes nothing. |
| `web-react/src/helpers/settings/controllerTypes.gen.ts` | create (generated) | Per-controller config interfaces. |
| `web-react/src/helpers/settings/settingsDefaults.gen.ts` | create (generated) | The settings tree's static defaults as a frozen constant. |
| `web-react/src/helpers/settings/paths.ts` | create | `SettingsPath` / `ValueAt` template-literal path types. Types only, no runtime code. |
| `web-react/src/helpers/settings/delta.ts` | modify | `setPath` gains the path/value type parameters. Runtime body unchanged. |
| `web-react/src/helpers/settings/settingsApi.ts` | modify | `applySettings` carries a structured `errors` array through. |
| `web-react/src/helpers/settings/useSaveSettings.ts` | modify | Exposes `errors` beside `status`. |
| `web-react/src/helpers/settings/fieldErrors.ts` | create | Maps a dotted error path to a tab's field id. Pure. |
| `common/settings_schema.py` | modify | A pairs-returning sibling of `_format_errors`. |
| `blueprints/api/routes.py` | modify | Both rejection envelopes gain `errors`. |
| `common/datastore.py` | modify | Observe-only settings validation at `init()`. |
| `web-react/src/components/settings/fields/*.tsx` | modify | `aria-describedby`, integer coercion. |

---

## Slice A — the generation chain

### Task 1: Emit per-controller config types

**Files:**
- Create: `web-react/scripts/emitControllerTypes.ts`
- Create test: `web-react/tests/unit/scripts/emitControllerTypes.test.ts`
- Modify: `web-react/scripts/gen-types.ts`
- Create (generated, committed): `web-react/src/helpers/settings/controllerTypes.gen.ts`
- Modify: `web-react/package.json` (no new script — `gen:types` drives all emitters)
- Modify: `docs/superpowers/backlogs/react-migration-backlog.md`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `emitControllerTypes(manifest: {metadata: Record<string, {config: ControllerOptionDecl[]}>}): string`, and the generated
  `ControllerConfigs` / `PidConfig` / `MpcConfig` / … types that Task 2 imports
  from `../../helpers/settings/controllerTypes.gen`.

**Context.** `common/settings_schema.py:337` models the field as
`config: dict[str, dict[str, float | int | bool | str]]`, which compiles to
`interface Config { [k: string]: { [k: string]: number | boolean | string } }`.
`controller/controllers.json` declares the real shape: nine controllers under
`metadata`, each with a `config` array of option declarations. `fuzzy` and `ml`
declare zero options.

**The Python side does not change.** A controller can be added by dropping a
file in `controller/`, so the server must keep accepting a tree that names one
it has never heard of. This is additive typing on the client only.

- [ ] **Step 1: Write the failing test**

Create `web-react/tests/unit/scripts/emitControllerTypes.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { emitControllerTypes } from "../../../scripts/emitControllerTypes";

const MANIFEST = {
  metadata: {
    pid: {
      config: [
        { option_name: "PB", option_type: "float" },
        { option_name: "Td", option_type: "float" },
      ],
    },
    pid_parallel: {
      config: [
        { option_name: "Kp", option_type: "float" },
        { option_name: "Clamping", option_type: "bool" },
      ],
    },
    fuzzy: { config: [] },
    mpc: {
      config: [
        { option_name: "n_horizon", option_type: "int" },
        { option_name: "policy_net_path", option_type: "string" },
        {
          option_name: "estimator",
          option_type: "list",
          list_values: ["ekf", "mhe", "kf"],
        },
      ],
    },
  },
};

describe("emitControllerTypes", () => {
  it("maps each declared option type to its TypeScript counterpart", () => {
    const out = emitControllerTypes(MANIFEST);
    expect(out).toContain("export interface PidConfig {\n  PB: number;\n  Td: number;\n}");
    expect(out).toContain("Kp: number;");
    expect(out).toContain("Clamping: boolean;");
    expect(out).toContain("n_horizon: number;");
    expect(out).toContain("policy_net_path: string;");
  });

  it("turns a list option into a union of its declared values, not a bare string", () => {
    // A typo'd estimator name must fail to compile; `string` would accept it.
    expect(emitControllerTypes(MANIFEST)).toContain(
      'estimator: "ekf" | "mhe" | "kf";',
    );
  });

  it("gives a controller that declares no options an empty record, not an index signature", () => {
    // Record<string, never> keeps `fuzzy` closed. An index signature would put
    // back exactly the looseness this emitter exists to remove.
    expect(emitControllerTypes(MANIFEST)).toContain(
      "export type FuzzyConfig = Record<string, never>;",
    );
  });

  it("maps every controller into one keyed interface", () => {
    const out = emitControllerTypes(MANIFEST);
    expect(out).toContain("export interface ControllerConfigs {");
    expect(out).toContain("  pid: PidConfig;");
    expect(out).toContain("  pid_parallel: PidParallelConfig;");
    expect(out).toContain("  fuzzy: FuzzyConfig;");
    expect(out).toContain("  mpc: MpcConfig;");
  });

  it("carries the do-not-edit banner", () => {
    expect(emitControllerTypes(MANIFEST)).toContain("do not edit");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/scripts/emitControllerTypes.test.ts`

Expected: FAIL — cannot resolve `../../../scripts/emitControllerTypes`.

- [ ] **Step 3: Write the emitter**

Create `web-react/scripts/emitControllerTypes.ts`:

```ts
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/scripts/emitControllerTypes.test.ts`

Expected: PASS, 5 tests.

- [ ] **Step 5: Wire it into `gen-types.ts` with a drift gate**

`web-react/scripts/gen-types.ts` currently generates one artifact. Replace its
body so each artifact is one entry in a table. Read the existing file first —
keep `SCHEMA_PATH`, `OUT_PATH` and `BANNER_COMMENT` semantics for the settings
types exactly as they are.

```ts
import { compileFromFile } from "json-schema-to-typescript";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { emitControllerTypes } from "./emitControllerTypes";

const SCHEMA_PATH = "schema/settings.schema.json";
const CONTROLLERS_PATH = "../controller/controllers.json";
const BANNER_COMMENT =
  "/* eslint-disable */\n" +
  "// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types";

interface Artifact {
  out: string;
  generate: () => Promise<string>;
}

const ARTIFACTS: Artifact[] = [
  {
    out: "src/helpers/settings/settingsTypes.gen.ts",
    generate: () => compileFromFile(SCHEMA_PATH, { bannerComment: BANNER_COMMENT }),
  },
  {
    out: "src/helpers/settings/controllerTypes.gen.ts",
    generate: async () =>
      emitControllerTypes(JSON.parse(await readFile(CONTROLLERS_PATH, "utf8"))),
  },
];

async function main() {
  const check = process.argv.includes("--check");
  let stale = false;

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

  if (stale) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

Note the temp-file machinery (`mkdtemp`/`rm`) in the old `--check` path is gone:
it wrote the candidate to a temp file and then compared strings in memory
anyway. Drop the now-unused `mkdtemp`, `tmpdir`, `join` and `rm` imports.

- [ ] **Step 6: Generate and verify the artifact**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run gen:types
bun run gen:types:check
```

Expected: both `.gen.ts` files written, then both reported up to date. Open
`src/helpers/settings/controllerTypes.gen.ts` and confirm `MpcConfig` has 27
fields, `PidConfig` has 4, and `FuzzyConfig`/`MlConfig` are
`Record<string, never>`.

- [ ] **Step 7: Correct the backlog entry that scheduled this**

In `docs/superpowers/backlogs/react-migration-backlog.md`, item 10's schema/toolchain
section records `additionalProperties` stripping as open. Replace that claim
with the evidence:

```markdown
`additionalProperties` stripping is **DONE**, and was closed by S2 rather than
by this work. `_Section` is `extra="forbid"`, so every modelled section emits
`"additionalProperties": false` and generates a **closed** interface — a typo'd
field already fails `bun run typecheck`. Exactly six nodes carry
`additionalProperties: true`, all of them deliberately dynamic maps
(`dashboard.dashboards`, `display.config`, `onesignal.devices`, the
`probe_devices`/`probe_info` items, `probe_profiles`); the rest are
`dict[str, X]` value schemas, which must generate an index signature —
`history_page.probe_config` becoming `{[k: string]: ProbeChartConfig}` is
correct output, not leakage. Nothing was left to strip, and stripping the
`dict[str, X]` form would have been a regression.
```

- [ ] **Step 8: Run the gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint
```

Expected: all pass; lint 0 errors / 2 pre-existing warnings.

- [ ] **Step 9: Commit**

```bash
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
feat(web): generate a config type per controller, from the manifest itself

settings.controller.config is a loose dict on the server for a good reason -- a
controller is added by dropping a file in controller/ -- but the client indexes
into it knowing exactly which one is selected, and had only
{[k: string]: number | boolean | string} to do it with.

controllers.json already declares every option and its type, so the types come
from there. A list option becomes a union of its declared values, so a typo'd
estimator name stops compiling rather than being rejected at runtime.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 2: `ControllerTab` consumes the generated types

**Files:**
- Modify: `web-react/src/components/settings/tabs/ControllerTab.tsx`
- Test: `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx`

**Interfaces:**
- Consumes: `ControllerConfigs` from
  `../../../helpers/settings/controllerTypes.gen` (Task 1).
- Produces: nothing later tasks depend on.

**Context.** Read `ControllerTab.tsx` in full before editing. It renders options
from the runtime `/api/controller_metadata` response (`ControllerOption` in
`helpers/settings/settingsApi.ts:21-34`) — that stays, because the option
*list* is a runtime fact. What changes is how the value bag it writes back to
`settings.controller.config[selected]` is checked.

**Corrected during execution, 2026-08-02 — the check is a runtime one, not a
compile-time one.** This task was first written expecting the generated types to
check the tab's value bag. They cannot: `selected` is a runtime string, so
`ControllerConfigs[selected]` never resolves statically, and an `as
SelectedConfig` assertion catches nothing — probed against the real generated
types with `tsc --strict`, both `{} as SelectedConfig` and
`{totallyBogusField: "nope"} as SelectedConfig` compile clean, while the
equivalent *assignment* fails with TS2322. This is the same fact that keeps
`controller.config` an open dict server-side: a controller can be added by
dropping a file into `controller/`.

So the boundary is validated at run time instead, against the option names the
tab already holds from `/api/controller_metadata`: a key the selected controller
does not declare is **filtered out of the payload** — writing it would put junk
into `settings.controller.config[selected]` that nothing cleans up — and
**surfaced to the user** through the tab's existing error path, never silently
dropped. The generated types keep their value where a controller is statically
known, and in the type-level tests below, which pin the generator's output.

- [ ] **Step 1: Write the failing type-level test**

Append to `web-react/tests/unit/components/settings/tabs/ControllerTab.test.tsx`:

```ts
// Type-level only: the generated map must name every controller and give each
// its own option set, so indexing one with another's option is a compile error.
// Runtime behaviour is unchanged and is covered by the cases above.
import type { ControllerConfigs } from "../../../../../src/helpers/settings/controllerTypes.gen";

describe("generated controller config types", () => {
  it("gives each controller its own option set", () => {
    const pid: ControllerConfigs["pid"] = { PB: 60, Td: 45, Ti: 180, center: 0.5 };
    expect(pid.PB).toBe(60);

    // @ts-expect-error -- Kp belongs to pid_parallel, not pid
    const wrong: ControllerConfigs["pid"] = { PB: 60, Td: 45, Ti: 180, center: 0.5, Kp: 1 };
    expect(wrong).toBeTruthy();
  });

  it("constrains a list option to its declared values", () => {
    const ok: ControllerConfigs["mpc"]["estimator"] = "ekf";
    expect(ok).toBe("ekf");

    // @ts-expect-error -- "ekfx" is not one of ekf | mhe | kf
    const typo: ControllerConfigs["mpc"]["estimator"] = "ekfx";
    expect(typo).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run typecheck to verify the assertions bite**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck`

Expected: PASS. A `@ts-expect-error` that has nothing to suppress is itself an
error ("Unused '@ts-expect-error' directive"), so a pass here proves both
constraints are real. **If typecheck passes but you suspect the directive is
inert, delete one `@ts-expect-error` line and re-run — it must then FAIL.** Put
it back.

- [ ] **Step 3: Type the tab's config bag**

In `ControllerTab.tsx`, replace the loose index type on whatever holds the
selected controller's values. The values arrive from the server as `unknown`
shapes, so narrow at the boundary rather than casting at every use:

```tsx
import type { ControllerConfigs } from "../../../helpers/settings/controllerTypes.gen";

/** The selected controller's option values. Keyed by controller name because
 *  the tab edits one at a time and writes back under that key. */
type SelectedConfig = ControllerConfigs[keyof ControllerConfigs];
```

Apply it to the state that currently holds `Record<string, number | boolean | string>`.
Where a value must be read generically (the render loop walks the runtime option
list), keep the existing runtime access and cast once, at that single point,
with a comment saying why:

```tsx
// The option LIST is a runtime fact from /api/controller_metadata; the generated
// types describe the value bag. Reading a runtime-named key out of a statically
// typed bag is the one place those two views meet.
const raw = (values as Record<string, number | boolean | string>)[option.option_name];
```

- [ ] **Step 4: Run the tab's tests and the gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test tests/unit/components/settings/tabs/ControllerTab.test.tsx
bun run typecheck && bun run lint
```

Expected: all existing ControllerTab cases still pass, plus the two new ones.

- [ ] **Step 5: Commit**

```bash
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
feat(web): let the controller tab read a typed option bag

The generated per-controller types replace the index signature the tab had been
reaching into. The option LIST still comes from /api/controller_metadata at
runtime -- which controller options exist is a property of the install -- so the
one place a runtime-named key meets a statically typed bag is narrowed there and
nowhere else.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 3: Emit the settings defaults constant

**Files:**
- Create: `web-react/scripts/emitSettingsDefaults.ts`
- Create test: `web-react/tests/unit/scripts/emitSettingsDefaults.test.ts`
- Modify: `web-react/scripts/gen-types.ts`
- Create (generated, committed): `web-react/src/helpers/settings/settingsDefaults.gen.ts`

**Interfaces:**
- Consumes: the `ARTIFACTS` table from Task 1.
- Produces: `emitSettingsDefaults(schema: object): string`, and the generated
  `SETTINGS_DEFAULTS` constant that Task 4 imports from
  `../../../helpers/settings/settingsDefaults.gen`.

**Context — this is simpler than it looks.** `web-react/schema/settings.schema.json`
carries a **fully resolved, nested** `default` on each top-level section:
`properties.startup.default` is the whole `{duration, prime_on_startup,
smartstart: {...}, start_to_mode: {...}}` object, not a `$ref`. Three of the 22
sections have no default and must be skipped — `lastupdated`, `server_info` and
`versions`, all of which are generated per install rather than defaulted.

- [ ] **Step 1: Write the failing test**

Create `web-react/tests/unit/scripts/emitSettingsDefaults.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { emitSettingsDefaults } from "../../../scripts/emitSettingsDefaults";

const SCHEMA = {
  properties: {
    shutdown: { default: { auto_power_off: false, shutdown_duration: 240 } },
    startup: {
      default: { duration: 240, smartstart: { enabled: false, exit_temp: 120 } },
    },
    versions: { $ref: "#/$defs/Versions" },
    server_info: { $ref: "#/$defs/ServerInfo" },
  },
};

describe("emitSettingsDefaults", () => {
  it("emits each section's resolved default, nested", () => {
    const out = emitSettingsDefaults(SCHEMA);
    expect(out).toContain('"shutdown_duration": 240');
    expect(out).toContain('"exit_temp": 120');
  });

  it("skips sections the schema gives no default", () => {
    // versions/server_info/lastupdated are generated per install -- emitting a
    // default for them would invent a value the backend never produces.
    const out = emitSettingsDefaults(SCHEMA);
    expect(out).not.toContain("versions");
    expect(out).not.toContain("server_info");
  });

  it("exports a frozen constant, so a consumer cannot mutate shared defaults", () => {
    const out = emitSettingsDefaults(SCHEMA);
    expect(out).toContain("export const SETTINGS_DEFAULTS =");
    expect(out).toContain("as const");
  });

  it("carries the do-not-edit banner", () => {
    expect(emitSettingsDefaults(SCHEMA)).toContain("do not edit");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/scripts/emitSettingsDefaults.test.ts`

Expected: FAIL — cannot resolve `../../../scripts/emitSettingsDefaults`.

- [ ] **Step 3: Write the emitter**

Create `web-react/scripts/emitSettingsDefaults.ts`:

```ts
// The settings tree's static defaults, as the schema already resolves them.
// Each top-level section carries a fully nested `default`; the three that do not
// (versions, server_info, lastupdated) are generated per install and have no
// static value to publish.

const BANNER =
  "/* eslint-disable */\n" +
  "// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types";

interface SchemaLike {
  properties: Record<string, { default?: unknown }>;
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/scripts/emitSettingsDefaults.test.ts`

Expected: PASS, 4 tests.

- [ ] **Step 5: Add it to the artifact table**

In `web-react/scripts/gen-types.ts`, add a third entry to `ARTIFACTS`:

```ts
  {
    out: "src/helpers/settings/settingsDefaults.gen.ts",
    generate: async () =>
      emitSettingsDefaults(JSON.parse(await readFile(SCHEMA_PATH, "utf8"))),
  },
```

and `import { emitSettingsDefaults } from "./emitSettingsDefaults";` at the top.

- [ ] **Step 6: Generate and confirm the values**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run gen:types && bun run gen:types:check
grep -A 3 '"shutdown"' src/helpers/settings/settingsDefaults.gen.ts
```

Expected: `shutdown_duration: 240`. **19 sections** present; `versions`,
`server_info` and `lastupdated` absent.

- [ ] **Step 7: Run the gates and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
feat(web): generate the settings defaults the tabs have been guessing at

Every static default already reaches the committed schema, fully resolved and
nested, on its section's `default` key. It is now emitted as a TypeScript
constant so the settings tabs have something to fall back to that is checked.

The three sections with no static default -- versions, server_info, lastupdated,
each generated per install -- are skipped rather than given an invented one.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 4: Point the tabs' fallbacks at the generated defaults

**Files:**
- Modify: `web-react/src/components/settings/tabs/StartupTab.tsx:51-70`
- Modify: the other settings tabs carrying `??` fallbacks
- Create test: `web-react/tests/unit/components/settings/defaultsParity.test.ts`

**Interfaces:**
- Consumes: `SETTINGS_DEFAULTS` from
  `../../../helpers/settings/settingsDefaults.gen` (Task 3).
- Produces: nothing later tasks depend on.

**This task requires judgement, not a sweep.** There are ~94 `??` sites across
the tabs and they are **two different things**:

1. **A fallback for a missing key** — "the store did not carry this field, so
   show what a fresh install would have." These must be `SETTINGS_DEFAULTS`.
2. **A placeholder for a disabled value** — StartupTab renders
   `exit_temp_default: (st.startup_exit_temp ?? 0) > 0 ? (st.startup_exit_temp as number) : 140`,
   where `0` means "disabled" and `140` is the value to pre-fill the box with
   when the user enables it. That 140 is a **UI choice** and must not become a
   schema default.

Replace category 1 only. When unsure which a site is, read what consumes the
value: if it feeds a `NumberField`'s `value`, it is category 1; if it feeds a
separate `*_default` used only when the real value is 0, it is category 2.

**The drift this fixes is real, not hygiene.** Measured against the schema:

| Site | Tab says | Schema default |
|---|---|---|
| `shutdown.shutdown_duration` | `?? 60` | **240** |
| `startup.duration` | `?? 60` | **240** |
| `startup.pwm_duty_cycle` | `?? 50` | **100** |
| `startup.smartstart.exit_temp` | `?? 150` | **120** |
| `startup.prime_on_startup` | `?? 0` | 0 — correct |

`startup.startup_exit_temp ?? 150` looks like drift (the schema default is `0`)
but is **category 2**: `0` means the exit-temp check is disabled, and `150` is
the box's pre-fill. Leave it, and add a comment saying it is a pre-fill.

- [ ] **Step 1: Write the failing parity test**

Create `web-react/tests/unit/components/settings/defaultsParity.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { SETTINGS_DEFAULTS } from "../../../../src/helpers/settings/settingsDefaults.gen";

// The values the tabs fall back to when the store carries no key. Pinned here
// so a hand-typed literal cannot drift from the schema again: every entry is a
// path a tab reads, and the expected value comes from the generated constant.
describe("tab fallbacks match the schema", () => {
  it("carries the durations the tabs need", () => {
    expect(SETTINGS_DEFAULTS.shutdown.shutdown_duration).toBe(240);
    expect(SETTINGS_DEFAULTS.startup.duration).toBe(240);
  });

  it("carries the startup values the tabs need", () => {
    expect(SETTINGS_DEFAULTS.startup.pwm_duty_cycle).toBe(100);
    expect(SETTINGS_DEFAULTS.startup.smartstart.exit_temp).toBe(120);
    expect(SETTINGS_DEFAULTS.startup.prime_on_startup).toBe(0);
  });
});
```

- [ ] **Step 2: Run it to confirm the generated constant really holds these**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/components/settings/defaultsParity.test.ts`

Expected: PASS. This test does not fail first — it pins Task 3's output so a
later schema change that moves a default is visible here. If it FAILS, Task 3's
emitter is wrong; fix that before continuing.

- [ ] **Step 3: Rewrite StartupTab's category-1 fallbacks**

In `web-react/src/components/settings/tabs/StartupTab.tsx`, add the import and
replace the literals:

```tsx
import { SETTINGS_DEFAULTS } from "../../../helpers/settings/settingsDefaults.gen";

const SHUTDOWN_DEFAULTS = SETTINGS_DEFAULTS.shutdown;
const STARTUP_DEFAULTS = SETTINGS_DEFAULTS.startup;
```

then, in the read block:

```tsx
    shutdown_duration: sh.shutdown_duration ?? SHUTDOWN_DEFAULTS.shutdown_duration,
    duration: st.duration ?? STARTUP_DEFAULTS.duration,
    prime_on_startup: st.prime_on_startup ?? STARTUP_DEFAULTS.prime_on_startup,
    pwm_duty_cycle: st.pwm_duty_cycle ?? STARTUP_DEFAULTS.pwm_duty_cycle,
    smartstart_exit_temp: ss.exit_temp ?? STARTUP_DEFAULTS.smartstart.exit_temp,
```

Leave `startup_exit_temp` and the two `*_default` lines alone, and give them the
comment that says what they are:

```tsx
    // Pre-fill for the box, not a schema default: the stored 0 means the
    // exit-temp check is off, and this is the value to offer when it is turned on.
    exit_temp_default: (st.startup_exit_temp ?? 0) > 0 ? (st.startup_exit_temp as number) : 140,
```

- [ ] **Step 4: Sweep the remaining tabs**

For each of `GeneralTab`, `HistoryTab`, `PelletsTab`, `PwmTab`, `SafetyTab`,
`WorkModeTab`, `NotificationsTab`, `UnitsTab`, `ProbesTab`, `PlatformTab`,
`ControllerTab`: list its `??` sites, classify each, and convert category 1.

```bash
cd /home/dannyb/sources/PiFire/web-react
grep -n "?? " src/components/settings/tabs/GeneralTab.tsx
```

Convert one tab per edit and run that tab's test file after each, so a
regression names the tab that caused it.

- [ ] **Step 5: Run the full suite and the gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint
```

Expected: all pass. **Tab tests asserting a displayed default will change** —
e.g. a test expecting "60" for startup duration now sees "240". That is the bug
being fixed: update the assertion to the schema value, and do **not** re-pin the
old literal.

- [ ] **Step 6: Commit**

```bash
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
fix(web): make the settings tabs fall back to the real defaults

The tabs carried hand-typed fallbacks that matched nothing, and five of the six
on the Startup tab were simply wrong: a missing shutdown duration showed 60
where a fresh install has 240, a missing startup duty cycle showed 50 where it
has 100. They read from the generated defaults now.

Not every ?? was one of these. Where the literal is the value to pre-fill a box
with once a disabled setting is switched on -- a stored 0 meaning "off" -- it is
a choice this UI makes and it stays, now saying so.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 5: Type `setPath`

**Files:**
- Create: `web-react/src/helpers/settings/paths.ts`
- Modify: `web-react/src/helpers/settings/delta.ts`
- Create test: `web-react/tests/unit/helpers/settings/paths.test.ts`

**Interfaces:**
- Consumes: `SettingsSchema` from `./settingsTypes.gen` (already committed).
- Produces: `SettingsPath` and `ValueAt<T, P>` from
  `helpers/settings/paths`, and the newly generic
  `setPath<P extends SettingsPath>(obj: object, path: P, value: ValueAt<Settings, P>): object`.

**Context.** `delta.ts` is the whole helper — a 12-line deep-path writer taking
`path: string, value: unknown`. There are **39 call sites** across 9 files
(`StartupTab` 13, `HistoryTab` 7, `PelletsTab` 7, `GeneralTab` 3, `WorkModeTab`
3, `ControllerTab` 2, `PwmTab` 2, `SafetyTab` 1, `accent.ts` 1). Today
`"startup.smartstart.exit_temp"` and `"startup.smartstart.exit_tmep"`
type-check identically, as do a `number` and a `boolean` for either.

**Enumerate them with serena's `find_referencing_symbols`, not grep.** Three of
the 39 are invisible to a line-oriented regex — one multi-line call and two that
pass the path as a variable — and Step 5 depends on classifying all of them.

**The runtime body does not change.** This is types only.

- [ ] **Step 1: Write the failing type-level test**

Create `web-react/tests/unit/helpers/settings/paths.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { setPath } from "../../../../src/helpers/settings/delta";

describe("setPath typing", () => {
  it("accepts a real path with a correctly typed value", () => {
    const out = setPath({}, "startup.duration", 240);
    expect(out).toEqual({ startup: { duration: 240 } });
  });

  it("accepts a deep path", () => {
    const out = setPath({}, "startup.smartstart.exit_temp", 120);
    expect(out).toEqual({ startup: { smartstart: { exit_temp: 120 } } });
  });

  it("rejects a misspelled path", () => {
    // @ts-expect-error -- exit_tmep is not a field of startup.smartstart
    const out = setPath({}, "startup.smartstart.exit_tmep", 120);
    expect(out).toBeTruthy();
  });

  it("rejects a value of the wrong type for a real path", () => {
    // @ts-expect-error -- startup.duration is a number
    const out = setPath({}, "startup.duration", true);
    expect(out).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run typecheck to verify the assertions fail as written**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck`

Expected: FAIL with "Unused '@ts-expect-error' directive" on both — proving the
current signature accepts a typo'd path and a wrong-typed value.

- [ ] **Step 3: Write the path types**

Create `web-react/src/helpers/settings/paths.ts`:

```ts
import type { SettingsSchema } from "./settingsTypes.gen";

/** A value that terminates a path rather than being descended into. Arrays are
 *  terminal: the tabs replace a whole list (temp_range_list, profiles) rather
 *  than addressing into one. */
type Leaf = string | number | boolean | null | undefined | readonly unknown[];

/** Every dotted path the settings tree admits, to any depth. */
export type PathsOf<T> = T extends Leaf
  ? never
  : {
      [K in keyof T & string]-?: NonNullable<T[K]> extends Leaf
        ? K
        : K | `${K}.${PathsOf<NonNullable<T[K]>>}`;
    }[keyof T & string];

/** The type stored at a dotted path. */
export type ValueAt<T, P extends string> = P extends `${infer Head}.${infer Rest}`
  ? Head extends keyof T
    ? ValueAt<NonNullable<T[Head]>, Rest>
    : never
  : P extends keyof T
    ? T[P]
    : never;

export type SettingsPath = PathsOf<SettingsSchema>;
```

- [ ] **Step 4: Make `setPath` generic**

`web-react/src/helpers/settings/delta.ts` — signature only; the body is
untouched:

```ts
import type { SettingsPath, ValueAt } from "./paths";
import type { SettingsSchema } from "./settingsTypes.gen";

export function setPath<P extends SettingsPath>(
  obj: object,
  path: P,
  value: ValueAt<SettingsSchema, P>,
): object {
  const keys = path.split(".");
  const root: Record<string, unknown> = { ...(obj as Record<string, unknown>) };
  let cur = root as Record<string, unknown>;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    cur[k] = { ...((cur[k] as Record<string, unknown>) ?? {}) };
    cur = cur[k] as Record<string, unknown>;
  }
  cur[keys[keys.length - 1]] = value;
  return root;
}
```

- [ ] **Step 5: Handle the eight paths that are built, not written**

**Enumerate the call sites with serena, not grep.** `find_referencing_symbols`
on `setPath` in `web-react/src/helpers/settings/delta.ts` is the only
enumeration that is complete here — a regex misses a multi-line call and a path
passed as a variable, and this plan's first draft missed three sites that way.

The 39 sites break down as **31 literal, 5 loop-key template, 3 runtime-keyed**.
The last eight do not compile against any `SettingsPath`, literal union or
derived: `` `pwm.${k}` `` where `k` is `string` narrows to nothing.

**The five loop keys — fix by typing the key, not by casting the path:**

| Site | Shape |
|---|---|
| `PwmTab.tsx:109` | ``Object.entries(clamped)`` → `` `pwm.${k}` `` |
| `SafetyTab.tsx:39` | ``Object.entries(v)`` → `` `safety.${k}` `` |
| `WorkModeTab.tsx:102` | ``Object.entries(v.cycle_data)`` → `` `cycle_data.${k}` `` |
| `WorkModeTab.tsx:103` | ``Object.entries(v.smoke_plus)`` → `` `smoke_plus.${k}` `` |
| `WorkModeTab.tsx:104` | ``Object.entries(v.keep_warm)`` → `` `keep_warm.${k}` `` |

`Object.entries` widens the key to `string`; `Object.keys` on a typed object
with an explicit key type keeps it:

```tsx
// Object.entries widens its key to string, which erases exactly the fact this
// loop depends on: every key of `clamped` is a field of settings.pwm.
type PwmKey = keyof NonNullable<SettingsSchema["pwm"]>;
for (const k of Object.keys(clamped) as PwmKey[]) {
  d = setPath(d, `pwm.${k}`, clamped[k]);
}
```

**The three runtime-keyed sites must NOT be forced.** Each indexes a dict that
is deliberately open server-side, keyed by a name discovered at runtime.
Narrowing them would assert a closed set that neither the backend nor the
install actually has:

| Site | Path | Keyed by |
|---|---|---|
| `ControllerTab.tsx:112` | `` `controller.config.${selected}` `` | the selected controller |
| `accent.ts:42` | `accentPath(settings)` → `` `display.config.${module}.accent_theme` `` | the installed display module |
| `GeneralTab.tsx:49` | the same `accentPath(settings)` value | the installed display module |

`accentPath` (`helpers/settings/accent.ts:9-12`) returns `string | null`, so
those two sites also carry a null check already. Leave all three casting, with
the reason on each:

```tsx
    // Genuinely dynamic: which controller is selected is a runtime fact, and
    // controller.config stays an open dict server-side so an install can add
    // one. Narrowing here would claim otherwise.
    d = setPath(d, `controller.config.${selected}` as SettingsPath, rebuilt as never);
```

```tsx
    // display.config is keyed by the installed display module and stays an open
    // dict for the same reason controller.config does.
    if (path) delta = setPath(delta, path as SettingsPath, v.accent_theme as never);
```

Run `bun run typecheck` after this step and before Step 6 — these eight are
where it will fail first.

- [ ] **Step 6: Run typecheck and time it**

```bash
cd /home/dannyb/sources/PiFire/web-react
time bun run typecheck
```

Expected: PASS, and the 39 existing call sites still compile.

**This is the plan's one real risk.** `PathsOf<SettingsSchema>` is a recursive
template-literal type over a 22-section tree. If typecheck **fails** with
"Type instantiation is excessively deep and possibly infinite", or its
wall-clock time regresses by more than roughly 2×, stop and take the fallback:

```ts
// Fallback for Step 6: a hand-written union of the paths actually written,
// instead of deriving every path the tree admits. Catches both error classes;
// costs an edit here when a tab writes a new path.
export type SettingsPath =
  | "shutdown.shutdown_duration"
  | "shutdown.auto_power_off"
  | "startup.duration"
  | "startup.startup_exit_temp";
  // ...one line per literal path. Enumerate with serena's
  // find_referencing_symbols on setPath, NOT with grep: PwmTab.tsx:113 is a
  // multi-line call whose path sits on line 116, and a line-oriented regex
  // drops it silently — leaving that one site failing to compile against a
  // union that looks complete.
```

`ValueAt` is unchanged either way. Record which route you took in the commit
message.

- [ ] **Step 7: Run the full suite and the gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint && bun run build
```

- [ ] **Step 8: Commit**

```bash
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
feat(web): type the settings delta writer's paths and values

setPath took a string and an unknown, so "startup.smartstart.exit_temp" and
"...exit_tmep" type-checked identically across its 39 call sites, and so did a
number and a boolean for either. The path is now constrained to the paths the
generated Settings type admits, and the value to whatever sits at that path.

The runtime body is untouched; this is entirely a change of signature.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

## Slice B — error plumbing

### Task 6: Send per-field errors, without changing `message`

**Files:**
- Modify: `common/settings_schema.py:691-697`
- Modify: `blueprints/api/routes.py:203-268`
- Create test: `tests/web/test_api_settings_error_detail.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all in `common/settings_schema.py`:
  `_error_pairs(errs: list[ErrorDetails]) -> list[dict]`,
  `format_validation_pairs(exc: ValidationError) -> list[dict]`,
  `validate_partial_settings_pairs(delta: dict) -> list[dict]`, and
  `SettingsValidationError.pairs: list[dict]`. Every entry is
  `{"path": "startup.duration", "message": "Input should be a valid integer"}`.
  Also the `errors` key on `/api/settings_update`'s envelope, which Task 7 reads.

**Context.** `_format_errors` (`common/settings_schema.py:691`) builds the dotted
paths and immediately flattens them:

```python
def _format_errors(errs: list[ErrorDetails]) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in errs]
```

`_api_post_settings_update` joins those with `"; "`. **`message` must stay
byte-identical** — the socket clients and any other consumer read it.

There are **four** rejection sites in `_api_post_settings_update`: the unknown
flag, the Layer-1 `validate_partial_settings` result, the caught
`SettingsValidationError`, and the bare `except Exception`. Only the middle two
have paths; the other two send `"errors": []`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_api_settings_error_detail.py`:

```python
"""Per-field settings errors have to survive the trip to the browser.

The dotted paths exist at the source -- pydantic's `loc` -- and were flattened
into one string before anything could route them to a widget. `message` is
pinned byte-identical here because other clients read it.
"""


def _post(client, body):
    return client.post("/api/settings_update", json=body)


def test_a_bad_field_reports_its_own_path(client, ds):
    res = _post(client, {"settings": {"startup": {"duration": "not a number"}}, "flags": []})
    body = res.get_json()

    assert body["result"] == "error"
    assert body["errors"] == [
        {"path": "startup.duration", "message": body["errors"][0]["message"]}
    ]
    assert body["errors"][0]["path"] == "startup.duration"
    assert body["errors"][0]["message"]


def test_the_message_is_unchanged_by_the_new_field(client, ds):
    # Other consumers read `message`; adding `errors` must not reword it.
    res = _post(client, {"settings": {"startup": {"duration": "not a number"}}, "flags": []})
    body = res.get_json()

    joined = "; ".join(f"{e['path']}: {e['message']}" for e in body["errors"])
    assert body["message"] == f"Settings update failed: {joined}"


def test_two_bad_fields_report_two_entries(client, ds):
    res = _post(
        client,
        {
            "settings": {"startup": {"duration": "x"}, "pwm": {"frequency": "y"}},
            "flags": [],
        },
    )
    paths = {e["path"] for e in res.get_json()["errors"]}

    assert paths == {"startup.duration", "pwm.frequency"}


def test_a_rejection_with_no_field_sends_an_empty_list(client, ds):
    # An unknown flag is not about any field. An invented path would send the
    # UI to highlight a widget that is not at fault.
    res = _post(client, {"settings": {}, "flags": ["not_a_flag"]})
    body = res.get_json()

    assert body["result"] == "error"
    assert body["errors"] == []


def test_a_successful_write_carries_no_errors_key_content(client, ds):
    res = _post(client, {"settings": {"startup": {"duration": 240}}, "flags": []})
    body = res.get_json()

    assert body["result"] == "success"
    assert body.get("errors", []) == []
```

Check `tests/web/conftest.py` for the exact fixture names before running — this
file assumes `client` and `ds`. If the Flask client fixture is named something
else, use that name.

- [ ] **Step 2: Run the test to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_settings_error_detail.py -q`

Expected: FAIL — `KeyError: 'errors'`.

- [ ] **Step 3: Add the pairs-returning formatter**

In `common/settings_schema.py`, beside `_format_errors`:

```python
def _error_pairs(errs: list[ErrorDetails]) -> list[dict]:
    return [
        {"path": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
        for err in errs
    ]


def format_validation_pairs(exc: ValidationError) -> list[dict]:
    """`{"path": "section.field", "message": reason}` for a pydantic ValidationError.

    The structured twin of format_validation_errors: same paths, same order, not
    yet joined, so a caller can route each one to the field that produced it.
    """
    return _error_pairs(exc.errors())
```

`SettingsValidationError` carries only the joined strings today. Give it the
pairs as well, so the API layer does not have to re-split them:

```python
class SettingsValidationError(ValueError):
    """A settings tree (or delta) failed strict schema validation."""

    def __init__(self, errors: list[str], pairs: list[dict] | None = None):
        self.errors = errors
        self.pairs = pairs or []
        super().__init__("; ".join(errors))
```

Find every `raise SettingsValidationError(...)` and pass the pairs alongside.

`validate_partial_settings(delta) -> list[str]` (`common/settings_schema.py:834`)
keeps its signature — other callers depend on it. Add the sibling beside it:

```python
def validate_partial_settings_pairs(delta: dict) -> list[dict]:
    """`{"path": ..., "message": ...}` for a sparse delta; empty if it type-checks.

    The structured twin of validate_partial_settings, over the same
    PartialSettingsSchema and therefore the same Layer-1 rules: field types
    only, no cross-field validators, which on a sparse delta would run against
    static defaults rather than the store's real values.
    """
    try:
        PartialSettingsSchema.model_validate(delta, strict=True)
    except ValidationError as exc:
        return _error_pairs(exc.errors())
    return []
```

Read `validate_partial_settings`'s body before writing this — it does more than
a bare `model_validate` (see its docstring on the discriminator), and this
sibling must make the **same** call it does, differing only in what it formats.

- [ ] **Step 4: Add `errors` to the four rejection sites**

In `blueprints/api/routes.py::_api_post_settings_update`, each `jsonify` gains
the key. The Layer-1 branch:

```python
    layer1_pairs = validate_partial_settings_pairs(delta)
    if layer1_pairs:
        message = "; ".join(f"{p['path']}: {p['message']}" for p in layer1_pairs)
        return jsonify({
            "result": "error",
            "message": f"Settings update failed: {message}",
            "errors": layer1_pairs,
            "data": {},
        }), 200
```

and the caught validation error:

```python
    except SettingsValidationError as exc:
        message = "; ".join(exc.errors)
        return jsonify({
            "result": "error",
            "message": f"Settings update failed: {message}",
            "errors": exc.pairs,
            "data": {},
        }), 200
```

The unknown-flag branch, the `guard_controller_selection` branch and the bare
`except Exception` each get `"errors": []`. The success return gets
`"errors": []` too, so the key is always present and a client never has to
distinguish absent from empty.

- [ ] **Step 5: Run the test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_settings_error_detail.py -q`

Expected: PASS, 5 tests.

- [ ] **Step 6: Run the whole Python suite**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`

Expected: `4712 passed, 4 skipped` (baseline 4707 + 5). Any other test that
asserts on the settings_update envelope may need the new key added to an
expected dict — update it; do not remove the key.

- [ ] **Step 7: Format, check and commit**

```bash
cd /home/dannyb/sources/PiFire
.venv/bin/ruff format common/settings_schema.py blueprints/api/routes.py tests/web/test_api_settings_error_detail.py
.venv/bin/ruff check common/ blueprints/ tests/
cat > /tmp/msg.txt <<'EOF'
feat(api): keep the field each settings error belongs to

pydantic reports which field failed and why; the dotted path was built and then
flattened into one semicolon-joined string, twice, before anything could route
it to the widget that caused it.

The envelope now carries the pairs alongside the joined message, which is
byte-identical to what it was -- the socket clients read it. The three
rejections that are not about any field send an empty list rather than an
invented path, which would point the UI at a widget that is not at fault.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 7: Carry `errors` through the client

**Files:**
- Modify: `web-react/src/helpers/settings/settingsApi.ts:69-86`
- Modify: `web-react/src/helpers/settings/useSaveSettings.ts`
- Test: `web-react/tests/unit/helpers/settings/settingsApi.test.ts`

**Interfaces:**
- Consumes: the `errors` key from Task 6.
- Produces: `SaveFieldError = { path: string; message: string }`; `applySettings`
  returns `{ok, message, errors: SaveFieldError[], data?}`; `useSaveSettings`
  returns `{save, saving, status, errors, baseUrl}` where `errors` is
  `SaveFieldError[]`. Task 8 consumes both.

- [ ] **Step 1: Write the failing test**

Append to `web-react/tests/unit/helpers/settings/settingsApi.test.ts` (create it
if absent, mirroring the mock style of the other helper tests):

```ts
describe("applySettings error detail", () => {
  it("returns the per-field errors the backend sent", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        result: "error",
        message: "Settings update failed: startup.duration: bad",
        errors: [{ path: "startup.duration", message: "bad" }],
      }),
    });

    const res = await applySettings("", {}, []);

    expect(res.ok).toBe(false);
    expect(res.errors).toEqual([{ path: "startup.duration", message: "bad" }]);
  });

  it("returns an empty list when the backend sends none", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ result: "error", message: "Unknown flag: nope" }),
    });

    expect((await applySettings("", {}, [])).errors).toEqual([]);
  });

  it("returns an empty list for a transport failure", async () => {
    fetchMock.mockRejectedValue(new Error("network error"));

    const res = await applySettings("", {}, []);

    expect(res.ok).toBe(false);
    expect(res.errors).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/helpers/settings/settingsApi.test.ts`

Expected: FAIL — `res.errors` is `undefined`.

- [ ] **Step 3: Carry it through `settingsApi.ts`**

```ts
/** One field the backend refused, and why. `path` is dotted, matching the
 *  settings tree: "startup.duration". */
export interface SaveFieldError {
  path: string;
  message: string;
}

export async function applySettings(
  baseUrl: string,
  delta: object,
  flags: SettingsFlag[],
): Promise<{ ok: boolean; message: string; errors: SaveFieldError[]; data?: Settings }> {
  try {
    const res = await fetch(buildSettingsUrl(baseUrl, "settings_update"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: delta, flags }),
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}`, errors: [] };
    const body = (await res.json()) as {
      result?: string;
      message?: string;
      errors?: SaveFieldError[];
      data?: Settings;
    };
    return {
      ok: body.result === "success",
      message: body.message ?? "",
      errors: body.errors ?? [],
      data: body.data,
    };
  } catch (e) {
    return {
      ok: false,
      message: e instanceof Error ? e.message : "network error",
      errors: [],
    };
  }
}
```

- [ ] **Step 4: Expose it from `useSaveSettings`**

```ts
export function useSaveSettings() {
  const revalidator = useRevalidator();
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const [errors, setErrors] = useState<SaveFieldError[]>([]);
  const save = useCallback(
    async (delta: object, flags: SettingsFlag[]): Promise<boolean> => {
      setSaving(true);
      setStatus({ kind: "idle" }); // clear the previous outcome for this attempt
      setErrors([]);
      const r = await applySettings(BASE_URL, delta, flags);
      setSaving(false);
      setErrors(r.errors);
      setStatus(
        r.ok ? { kind: "saved" } : { kind: "error", message: normalizeSaveError(r.message) },
      );
      if (r.ok) revalidator.revalidate(); // re-run the loader → fresh settings
      return r.ok;
    },
    [revalidator],
  );
  return { save, saving, status, errors, baseUrl: BASE_URL };
}
```

`normalizeSaveError` keeps its current job: it is the summary line, and the only
thing shown for a rejection with no path.

- [ ] **Step 5: Run the tests and gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint
```

- [ ] **Step 6: Commit**

```bash
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
feat(web): stop discarding the backend's per-field save errors

applySettings read `message` and nothing else, so the paths the backend had just
gone to the trouble of sending were dropped at the boundary. They reach
useSaveSettings now, beside the summary line, which keeps its job as the one
thing shown when a rejection names no field.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 8: Point each error at the widget that caused it

**Files:**
- Create: `web-react/src/helpers/settings/fieldErrors.ts`
- Create test: `web-react/tests/unit/helpers/settings/fieldErrors.test.ts`
- Modify: `web-react/src/components/settings/fields/NumberField.tsx`
- Modify: `web-react/src/components/settings/tabs/StartupTab.tsx`
- Modify: `web-react/src/components/settings/SaveBar.tsx`

**Interfaces:**
- Consumes: `SaveFieldError` and `useSaveSettings().errors` (Task 7).
- Produces: `errorFor(errors: SaveFieldError[], path: string): string | null`
  and `unmatchedErrors(errors: SaveFieldError[], paths: string[]): SaveFieldError[]`
  from `helpers/settings/fieldErrors`; and an `error?: string | null` prop on
  `NumberField`.

**Context.** Each tab already names every path it writes, in its `setPath` calls
— `StartupTab` names 13. That is the map; no new registry is needed, only a
lookup keyed on the same string the field writes to.

- [ ] **Step 1: Write the failing test**

Create `web-react/tests/unit/helpers/settings/fieldErrors.test.ts`:

```ts
import { describe, expect, it } from "@rstest/core";
import { errorFor, unmatchedErrors } from "../../../../src/helpers/settings/fieldErrors";

const ERRORS = [
  { path: "startup.duration", message: "Input should be a valid integer" },
  { path: "pwm.frequency", message: "Input should be greater than 0" },
];

describe("errorFor", () => {
  it("finds the message for a path", () => {
    expect(errorFor(ERRORS, "startup.duration")).toBe("Input should be a valid integer");
  });

  it("returns null for a path with no error", () => {
    expect(errorFor(ERRORS, "startup.pwm_duty_cycle")).toBeNull();
  });

  it("returns null when there are no errors at all", () => {
    expect(errorFor([], "startup.duration")).toBeNull();
  });
});

describe("unmatchedErrors", () => {
  it("returns the errors no widget on this tab claims", () => {
    // A cross-section rule can reject a path the current tab does not render.
    // Dropping it silently would leave a failed save with nothing on screen.
    expect(unmatchedErrors(ERRORS, ["startup.duration"])).toEqual([
      { path: "pwm.frequency", message: "Input should be greater than 0" },
    ]);
  });

  it("returns nothing when every error is claimed", () => {
    expect(unmatchedErrors(ERRORS, ["startup.duration", "pwm.frequency"])).toEqual([]);
  });

  it("returns everything when the tab claims nothing", () => {
    expect(unmatchedErrors(ERRORS, [])).toEqual(ERRORS);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/helpers/settings/fieldErrors.test.ts`

Expected: FAIL — cannot resolve the module.

- [ ] **Step 3: Write the lookup**

Create `web-react/src/helpers/settings/fieldErrors.ts`:

```ts
import type { SaveFieldError } from "./settingsApi";

/** The backend's message for one settings path, or null if it did not reject it. */
export function errorFor(errors: SaveFieldError[], path: string): string | null {
  return errors.find((e) => e.path === path)?.message ?? null;
}

/** The errors no field on the current tab renders. They still have to be shown
 *  somewhere: a cross-section rule can reject a path this tab does not own, and
 *  a failed save with nothing on screen is worse than an unplaced message. */
export function unmatchedErrors(
  errors: SaveFieldError[],
  paths: string[],
): SaveFieldError[] {
  const claimed = new Set(paths);
  return errors.filter((e) => !claimed.has(e.path));
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/helpers/settings/fieldErrors.test.ts`

Expected: PASS, 6 tests.

- [ ] **Step 5: Give `NumberField` an error slot**

Add the prop and wire the ARIA. This is the same element Task 11 attaches
`aria-describedby` to — do that task first if you are running C out of order,
or accept a small merge here.

```tsx
export function NumberField({
  label, value, onChange, min, max, step, suffix, hint, disabled, error = null,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  hint?: string;
  disabled?: boolean;
  /** The backend's reason for refusing this field on the last save. */
  error?: string | null;
}) {
```

and in the markup, after the control:

```tsx
        {suffix && <span className="pf-field-suffix">{suffix}</span>}
      </span>
      {hint && <span className="pf-field-hint">{hint}</span>}
      {error && (
        <span className="pf-field-error" role="alert">
          {error}
        </span>
      )}
```

with `aria-invalid={error ? true : undefined}` on the `<input>`.

- [ ] **Step 6: Wire one tab end to end**

In `StartupTab.tsx`, take `errors` from `useSaveSettings()` and pass the match
to each field it writes:

```tsx
  const { save, saving, status, errors } = useSaveSettings();
```

```tsx
          <NumberField
            label="Startup Duration"
            value={v.duration}
            onChange={(n) => set("duration", n)}
            error={errorFor(errors, "startup.duration")}
            suffix="s"
          />
```

Do this for each of the tab's 13 written paths.

- [ ] **Step 7: Show what no field claimed**

In `SaveBar.tsx` (read it first), render `unmatchedErrors(errors, TAB_PATHS)`
beneath the existing status line, where `TAB_PATHS` is the list the tab passes
down. Keep the existing `status.message` rendering: it is the summary, and the
only thing shown for a rejection with no path at all.

- [ ] **Step 8: Add a component test**

Append to `web-react/tests/unit/components/settings/tabs/StartupTab.test.tsx`:

```tsx
it("puts the backend's rejection on the field that caused it", async () => {
  saveMock.mockResolvedValue(false);
  useSaveSettingsMock.mockReturnValue({
    save: saveMock,
    saving: false,
    status: { kind: "error", message: "startup.duration: Input should be a valid integer" },
    errors: [{ path: "startup.duration", message: "Input should be a valid integer" }],
    baseUrl: "",
  });

  renderRoute(<StartupTab />, {});

  expect(screen.getByRole("alert").textContent).toContain("Input should be a valid integer");
});
```

Match the existing mock idiom in that file — read it before writing; the mock
names above are illustrative of shape, not guaranteed to be what the file uses.

- [ ] **Step 9: Run the suite and gates, then commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint && bun run build
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
feat(web): show a refused setting's reason on the setting itself

The tabs already name every path they write, in their setPath calls, so a
rejection can be matched to the widget that produced it without a second
registry. A refused field is marked invalid and carries its reason.

Errors no field on the tab claims are still rendered, under the save bar: a
cross-section rule can reject a path this tab does not own, and a failed save
with nothing on screen is worse than an unplaced message.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

## Slice C — validation and the sweep

### Task 9: Validate the settings tree at `init()`, and only log

**Files:**
- Modify: `common/datastore.py:331-336`
- Create test: `tests/unit/datastore/test_read_path_validation.py`

**Interfaces:**
- Consumes: `validate_settings_tree` / `SettingsValidationError` from
  `common/settings_schema.py`.
- Produces: `_validate_settings_in_store()` in `common/datastore.py`, called from
  `init()` after `_upgrade_pellets_in_store()`.

**Context.** `read_settings()` (`common/datastore_accessors.py:407`) returns the
store's dict untouched; nothing on the read path has ever looked at the tree.

**It goes in `init()`, not in `read_settings()`.** `init()` runs in all three
processes (`app.py:39`, `control.py:70`, `display_process.py:51`), so every
process start is checked at no steady-state cost — validating each read would
tax the control loop's hot path.

**It observes and does not enforce.** It must not raise, strip, or normalise.
With write-gating, the migration registry and the shape digest all in place, a
failure here means a hand-edited database, a downgrade, or a migration bug — all
worth reporting, none worth refusing to boot a grill over, possibly mid-cook.

It runs **after** the migration steps, so anything it reports is something
migrations could not fix.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/datastore/test_read_path_validation.py`:

```python
"""init() reports a settings tree that does not match the models, and does
nothing else about it.

Write-gating, the migration registry and the shape digest between them mean a
tree that fails here got that way outside this code -- a hand-edited database, a
downgrade, a migration bug. Each is worth a log line and none is worth refusing
to start a control loop over.
"""

from unittest import mock

from common import datastore
from common.datastore_accessors import read_settings, write_settings_store


def test_a_valid_tree_reports_nothing(ds, caplog):
    with caplog.at_level("WARNING"):
        datastore._validate_settings_in_store()

    assert "settings" not in caplog.text.lower()


def test_a_broken_tree_is_reported_with_its_paths(ds, caplog):
    settings = read_settings()
    settings["startup"]["duration"] = "not a number"
    write_settings_store(settings)  # bypasses the write gate on purpose

    with caplog.at_level("WARNING"):
        datastore._validate_settings_in_store()

    assert "startup.duration" in caplog.text


def test_a_broken_tree_does_not_raise(ds):
    settings = read_settings()
    settings["startup"]["duration"] = "not a number"
    write_settings_store(settings)

    datastore._validate_settings_in_store()  # must not raise


def test_a_broken_tree_is_left_exactly_as_it_was(ds):
    # Observe-only: no stripping, no coercion, no normalised dump written back.
    settings = read_settings()
    settings["startup"]["duration"] = "not a number"
    write_settings_store(settings)

    datastore._validate_settings_in_store()

    assert read_settings()["startup"]["duration"] == "not a number"


def test_init_runs_it_after_the_migrations(ds):
    # Ordering matters: anything it reports has to be something the migration
    # steps could not fix, or every pre-migration tree would log on every boot.
    calls = []
    with (
        mock.patch.object(datastore, "connection", lambda: calls.append("connection")),
        mock.patch.object(datastore, "_drop_legacy_error_blobs", lambda: calls.append("drop")),
        mock.patch.object(datastore, "_first_boot_import", lambda: calls.append("import")),
        mock.patch.object(datastore, "_upgrade_settings_in_store", lambda: calls.append("settings")),
        mock.patch.object(datastore, "_upgrade_pellets_in_store", lambda: calls.append("pellets")),
        mock.patch.object(datastore, "_validate_settings_in_store", lambda: calls.append("validate")),
    ):
        datastore.init()

    assert calls == ["connection", "drop", "import", "settings", "pellets", "validate"]
```

`tests/unit/datastore/test_datastore.py` already has an `init()` ordering test
(`test_init_runs_the_upgrade`); read it and match its stubbing idiom, then add
`_validate_settings_in_store` to **its** expected order too, or it will fail.

- [ ] **Step 2: Run it to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/test_read_path_validation.py -q`

Expected: FAIL — `AttributeError: module 'common.datastore' has no attribute '_validate_settings_in_store'`.

- [ ] **Step 3: Implement it**

In `common/datastore.py`, after `_upgrade_pellets_in_store`:

```python
def _validate_settings_in_store():
    """Report a settings tree that does not match the models.

    Observes only: the tree is returned to every reader exactly as stored,
    whatever this finds. Runs after the migration steps, so a report here names
    something migrations could not repair -- a hand-edited database, a
    downgrade, or a gap in the registry -- rather than a tree merely waiting to
    be brought forward.
    """
    from common import settings_schema  # deferred to avoid import cycle
    from common.datastore_accessors import read_settings

    try:
        settings_schema.validate_settings_tree(read_settings())
    except settings_schema.SettingsValidationError as exc:
        write_log(
            "Stored settings do not match this build's schema: " + "; ".join(exc.errors)
        )
```

and add the call to `init()`:

```python
def init():
    connection()
    _drop_legacy_error_blobs()
    _first_boot_import()
    _upgrade_settings_in_store()
    _upgrade_pellets_in_store()
    _validate_settings_in_store()
```

Check what `common/datastore.py` already imports for logging — it uses
`write_log` elsewhere in this module; use the same one rather than introducing a
second logger.

- [ ] **Step 4: Run the test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/datastore/ -q`

Expected: PASS, including the amended `test_init_runs_the_upgrade`.

- [ ] **Step 5: Run the whole suite, format and commit**

```bash
cd /home/dannyb/sources/PiFire
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
.venv/bin/ruff format common/datastore.py tests/unit/datastore/test_read_path_validation.py
.venv/bin/ruff check common/ tests/
cat > /tmp/msg.txt <<'EOF'
feat(datastore): report a stored settings tree that does not match the models

Nothing on the read path had ever looked at the tree, so a store that did not
match reached the UI unremarked.

It runs at init() rather than in read_settings(): init() runs in all three
processes, so every process start is covered without taxing the control loop's
hot path. It observes and does not enforce -- a tree that fails here got that
way outside the write gate, and none of the ways that can happen is worth
refusing to start a control loop over, possibly mid-cook.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 10: `setTimeout` → `waitFor` in the tab tests

**Files:**
- Modify: 11 files under `web-react/tests/unit/components/settings/tabs/`

**Interfaces:** none — tests only.

**Context.** 48 sleeps across 11 files. `ProbesTab.test.tsx` already uses
`waitFor` (7 sites, 0 sleeps) and is the reference; `PlatformTab.test.tsx` has
neither and needs nothing.

| File | sleeps |
|---|---|
| `StartupTab.test.tsx` | 10 |
| `NotificationsTab.test.tsx` | 8 |
| `PwmTab.test.tsx` | 8 |
| `HistoryTab.test.tsx` | 5 |
| `GeneralTab.test.tsx` | 4 |
| `ControllerTab.test.tsx` | 3 |
| `PelletsTab.test.tsx` | 3 |
| `WorkModeTab.test.tsx` | 3 |
| `SafetyTab.test.tsx` | 2 |
| `UnitsTab.test.tsx` | 2 |

The idiom to replace, from `SafetyTab.test.tsx:94`:

```tsx
    fireEvent.click(saveButton);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(/* ... */);
```

becomes:

```tsx
    fireEvent.click(saveButton);
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith(/* ... */));
```

- [ ] **Step 1: Convert one file and prove the conversion is sound**

Start with `SafetyTab.test.tsx` (2 sites, smallest). Add `waitFor` to the
`@testing-library/react` import, convert both, and run:

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test tests/unit/components/settings/tabs/SafetyTab.test.tsx
```

Expected: PASS, same count as before.

- [ ] **Step 2: Prove the converted test still fails for the right reason**

Temporarily break the production code the test asserts on — in `SafetyTab.tsx`,
change one saved field's value — and re-run. It must FAIL. Revert.

A `waitFor` that passes because it wraps a vacuous assertion is worse than the
sleep it replaced; this step is what distinguishes the two.

- [ ] **Step 3: Convert the remaining ten files, one at a time**

After each file: `bun run test tests/unit/components/settings/tabs/<File>.test.tsx`.

**If a test only passes with a longer `waitFor` timeout, stop and record it.**
That is a real race in the component, not a conversion problem, and it must be
reported rather than tuned away.

- [ ] **Step 4: Confirm no sleeps remain**

```bash
cd /home/dannyb/sources/PiFire/web-react
grep -rn "setTimeout" tests/unit/components/settings/tabs/ | grep -v "waitFor"
```

Expected: no output.

- [ ] **Step 5: Run the suite and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run lint
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
test(settings): wait for the assertion instead of sleeping past it

Forty-eight fixed 50ms sleeps across eleven tab tests, each one a guess that the
save had landed. waitFor polls the assertion instead, so the tests neither flake
on a slow machine nor spend the wait on a fast one.

Each converted file was re-run against a deliberately broken component to
confirm the assertion still fails -- a waitFor wrapped around a vacuous
assertion is worse than the sleep it replaces.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 11: `aria-describedby` on the hints

**Files:**
- Modify: `web-react/src/components/settings/fields/NumberField.tsx`
- Modify: `web-react/src/components/settings/fields/Toggle.tsx`
- Modify: `web-react/src/components/settings/tabs/HistoryTab.tsx:174-188`
- Create test: `web-react/tests/unit/components/settings/fields/hints.test.tsx`

**Interfaces:**
- Consumes: `NumberField`'s `error` prop (Task 8) if that task landed first.
- Produces: a `hint` prop on `Toggle`.

**Context.** Zero occurrences of `aria-describedby` across all eight components
in `web-react/src/components/settings/fields/`. Two cases:

- `NumberField.tsx:52` renders `{hint && <span className="pf-field-hint">{hint}</span>}`
  as a sibling of the input, associated with nothing.
- `HistoryTab.tsx:181-187` renders the gated-toggle explanation ("Stop the grill
  to change extended-data logging") as a sibling of a **disabled** `Toggle`, and
  `Toggle.tsx` has no hint prop at all.

- [ ] **Step 1: Write the failing test**

Create `web-react/tests/unit/components/settings/fields/hints.test.tsx`:

```tsx
import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { NumberField } from "../../../../../src/components/settings/fields/NumberField";
import { Toggle } from "../../../../../src/components/settings/fields/Toggle";

describe("field hints are announced", () => {
  it("associates a NumberField's hint with its input", () => {
    render(
      <NumberField label="Sleep Timeout" value={300} onChange={rs.fn()} hint="0 = never sleep." />,
    );
    const input = screen.getByRole("spinbutton");
    const describedBy = input.getAttribute("aria-describedby");

    // The attribute alone proves nothing -- it has to resolve to the hint.
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toBe("0 = never sleep.");
  });

  it("leaves aria-describedby off a NumberField with no hint", () => {
    render(<NumberField label="Frequency" value={25000} onChange={rs.fn()} />);
    expect(screen.getByRole("spinbutton").getAttribute("aria-describedby")).toBeNull();
  });

  it("associates a Toggle's hint with its control", () => {
    render(
      <Toggle
        label="Extended Data Logging"
        checked={false}
        onChange={rs.fn()}
        disabled
        hint="Stop the grill to change extended-data logging"
      />,
    );
    const button = screen.getByRole("button", { name: "Extended Data Logging" });
    const describedBy = button.getAttribute("aria-describedby");

    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toBe(
      "Stop the grill to change extended-data logging",
    );
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/components/settings/fields/hints.test.tsx`

Expected: FAIL — `aria-describedby` is null, and `Toggle` rejects the `hint`
prop at typecheck.

- [ ] **Step 3: Wire `NumberField`**

```tsx
import { useId } from "react";
```

```tsx
  const hintId = useId();
```

on the input: `aria-describedby={hint ? hintId : undefined}`, and on the span:
`<span className="pf-field-hint" id={hintId}>{hint}</span>`.

- [ ] **Step 4: Give `Toggle` a hint**

```tsx
import { useId } from "react";

export function Toggle({
  label, checked, onChange, disabled, hint,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  /** Why the control is in the state it is — announced with it, not beside it. */
  hint?: string;
}) {
  const hintId = useId();
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <button
        type="button"
        className={`pf-switch ${checked ? "on" : ""}`}
        aria-pressed={checked}
        aria-describedby={hint ? hintId : undefined}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
      >
        <span className="pf-switch-knob" />
      </button>
      {hint && (
        <span className="pf-settings-hint" id={hintId}>
          {hint}
        </span>
      )}
    </label>
  );
}
```

- [ ] **Step 5: Move HistoryTab's hint onto the Toggle**

Replace the sibling `<span>` with the prop:

```tsx
        <div className="relative">
          <Toggle
            label="Extended Data Logging"
            checked={v.ext_data}
            onChange={(b) => set("ext_data", b)}
            disabled={ext_data_disabled}
            hint={
              ext_data_disabled
                ? modeUnknown
                  ? "Can't confirm the grill is stopped — extended-data logging stays locked"
                  : "Stop the grill to change extended-data logging"
                : undefined
            }
          />
        </div>
```

- [ ] **Step 6: Run the tests and gates**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint
```

`HistoryTab.test.tsx` may assert the hint text via its old markup — update the
query, keep the assertion.

- [ ] **Step 7: Commit**

```bash
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
feat(settings): announce a field's hint with the field

The hints sat next to their controls in the DOM and were connected to nothing,
so a screen reader reached a disabled toggle with no way to learn why it was
disabled. They are associated now, and the tests assert the association
resolves to the hint's text rather than merely that the attribute is present.

The gated extended-data toggle's explanation moves onto the Toggle itself,
which gains a hint of its own rather than having one rendered beside it.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

### Task 12: Integer fields must not emit floats

**Files:**
- Modify: `web-react/src/components/settings/fields/NumberField.tsx`
- Modify: the tabs that render integer-backed `NumberField`s
- Create test: `web-react/tests/unit/components/settings/fields/integerFields.test.tsx`

**Interfaces:**
- Consumes: `NumberField` as amended by Tasks 8 and 11.
- Produces: an `integer?: boolean` prop on `NumberField`.

**Context.** `NumberField.tsx:36` is `onChange(Number(e.target.value))`, so
typing `2.5` into a field backed by an `int` produces a float that the strict
backend rejects on save — with a message the user cannot connect to what they
typed. The clamp already runs on blur (`:44-48`); rounding belongs in the same
place, for the same reason clamping does: rounding on change makes the field
untypeable.

**Which fields are integers** comes from the schema. Enumerate them:

```bash
cd /home/dannyb/sources/PiFire
python3 - <<'PY'
import json
s = json.load(open("web-react/schema/settings.schema.json"))
for name, node in s.get("$defs", {}).items():
    for field, spec in node.get("properties", {}).items():
        ref = spec.get("$ref", "")
        if spec.get("type") == "integer" or ref.endswith("Int"):
            print(f"{name}.{field}")
PY
```

Cross-reference that list against every `<NumberField` in
`web-react/src/components/settings/tabs/`.

- [ ] **Step 1: Write the failing test**

Create `web-react/tests/unit/components/settings/fields/integerFields.test.tsx`:

```tsx
import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { NumberField } from "../../../../../src/components/settings/fields/NumberField";

describe("NumberField integer coercion", () => {
  it("rounds a typed fraction when the field is integer-backed", () => {
    // The backend is strict: 2.5 into an int field is refused on save, with a
    // message the user cannot connect to what they typed.
    const onChange = rs.fn();
    render(<NumberField label="P-Mode" value={2} onChange={onChange} integer min={0} max={9} />);

    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "2.5" } });
    fireEvent.blur(input);

    expect(onChange).toHaveBeenLastCalledWith(3);
  });

  it("leaves a fraction alone when the field is not integer-backed", () => {
    const onChange = rs.fn();
    render(<NumberField label="PB" value={60} onChange={onChange} />);

    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "60.5" } });
    fireEvent.blur(input);

    expect(onChange).not.toHaveBeenCalledWith(61);
  });

  it("still clamps an integer field to its bounds", () => {
    const onChange = rs.fn();
    render(<NumberField label="P-Mode" value={2} onChange={onChange} integer min={0} max={9} />);

    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "99" } });
    fireEvent.blur(input);

    expect(onChange).toHaveBeenLastCalledWith(9);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test tests/unit/components/settings/fields/integerFields.test.tsx`

Expected: FAIL — `integer` is not a prop, and 2.5 survives the blur.

- [ ] **Step 3: Implement**

Add `integer?: boolean` to the props, and extend the existing blur handler —
the comment already there explains why blur and not change; keep it:

```tsx
          onBlur={(e) => {
            const typed = Number(e.target.value);
            const rounded = integer ? Math.round(typed) : typed;
            const clamped = clampToBounds(rounded, min, max);
            if (clamped !== typed) onChange(clamped);
          }}
```

and `step={step ?? (integer ? 1 : undefined)}` so the spinner arrows agree with
the field.

- [ ] **Step 4: Mark the integer fields**

Add `integer` to each `<NumberField` whose schema type is `integer`, from the
enumeration above. `pwm.frequency`, `display.sleep_timeout`,
`shutdown.shutdown_duration`, `startup.duration` and the P-Mode fields are all
integers; `pid.PB`/`Td`/`Ti` and the MPC weights are not.

- [ ] **Step 5: Run everything and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test && bun run typecheck && bun run lint && bun run build
cd /home/dannyb/sources/PiFire
cat > /tmp/msg.txt <<'EOF'
fix(settings): keep an integer setting an integer

Typing 2.5 into a field the schema declares an integer sent a float, which the
strict backend refuses on save -- reporting a type error against a field the
user had every reason to think they had filled in correctly.

Integer-backed fields round when the field is left, in the same place and for
the same reason the bounds clamp there: rounding as each character arrives makes
the field untypeable.
EOF
jj describe --stdin < /tmp/msg.txt
jj new
```

---

## Parallelization

Slices are independent of each other. Within them:

| Task | Depends on | Can run beside |
|---|---|---|
| 1 (controller types) | — | 3, 6, 9, 10 |
| 2 (ControllerTab) | 1 | 3, 6, 9, 10 |
| 3 (defaults emitter) | 1 (shares `ARTIFACTS`) | 2, 6, 9, 10 |
| 4 (tabs use defaults) | 3 | 6, 9, 10 |
| 5 (typed setPath) | — (better after 2) | 6, 9, 10 |
| 6 (backend errors) | — | 1–5, 9, 10 |
| 7 (client carries errors) | 6 | 1–5, 9, 10 |
| 8 (path → widget) | 7 | 9, 10 |
| 9 (read-path validation) | — | everything |
| 10 (waitFor) | — | everything |
| 11 (aria-describedby) | — | 9, 10 |
| 12 (integer fields) | — | 9, 10 |

**Do not run 8, 11 and 12 concurrently** — all three edit
`NumberField.tsx`, and 8 and 11 both edit `HistoryTab.tsx`/the field markup.
Sequence them 11 → 12 → 8, or run them in one workspace.

Tasks 4 and 5 both edit every tab; do not run them concurrently either.

Concurrency needs **isolated jj workspaces**, not merely disjoint file lists.
Copy `.lsp.json` and run `bun install` in each new workspace — both are
gitignored, so `jj workspace add` skips them.

---

## Verification, end to end

After the last task in any slice:

```bash
cd /home/dannyb/sources/PiFire
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web-react
bun run gen:types:check && bun run typecheck && bun run lint && bun run test && bun run build
```

`tests/web/*.py` marked `[chromium]` **skip in agent worktrees** and run in the
main checkout. If a task touched anything under `tests/web/`, re-run those files
in the main checkout before calling the slice done — a green agent run may have
skipped them entirely.
