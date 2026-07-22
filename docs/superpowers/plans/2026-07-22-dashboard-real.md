# Dashboard-Real Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `web-react` dashboard run against the real PiFire backend (prototype mode): correct data contract, real control commands, honest connection/error states.

**Architecture:** Reads stream over the existing SocketIO `socket_dash_data` channel; writes go over REST using PiFire's authoritative command grammar (`/api/set|cmd/…`). No backend changes. `deriveView` stays a pure view-model mapper. New pure units (`command.ts`, rewritten `controlButtons.ts`, small helpers) are TDD'd; UI wiring is proven by a Playwright round-trip against the running prototype backend.

**Tech Stack:** React 19, TypeScript (strict), Vite 8, Vitest 4, socket.io-client, Playwright (from the main repo's `uv` env). Package manager: **bun**.

## Global Constraints

- Package manager is **bun**, never bare npm: `bun install`, `bun run dev|demo|build|test`. Commit `bun.lock`.
- TypeScript strict, `noUnusedLocals` + `noUnusedParameters` on. Typecheck: `bunx tsc -b`.
- Tests run under Vitest node env (no jsdom/RTL): `bun run test`. Only pure functions get unit tests; component behavior is covered by the Playwright round-trip.
- **Reads = socket, writes = REST command grammar.** No new/changed Python.
- Mode strings sent to the backend are **lowercase**; **Hold requires a temperature** (`/api/set/psp/{temp}`).
- Prototype backend launch (two processes, from repo root): `uv run python control.py` and `uv run python app.py` (serves `0.0.0.0:5000`). Both idempotently call `datastore.init()`.
- Vite dev proxies `/socket.io` **and** `/api` to `VITE_PIFIRE_URL || http://localhost:5000`.
- Demo mode (`VITE_DEMO=1`) must keep working as the offline path.
- All new frontend files live under `web-react/src/`. Work from `web-react/` for bun commands.

---

## File Structure

- `web-react/vite.config.ts` — add `/api` proxy (modify).
- `web-react/package.json` — add `dev:backend` note / no code (modify).
- `web-react/README.md` — document the prototype two-process launch (modify/create section).
- `web-react/src/types.ts` — expand `DashData`/`ProbeData` to the real payload; drop `SendCommand` (modify).
- `web-react/src/fixture.ts` — replace with a captured real `socket_dash_data` (modify).
- `web-react/src/demoData.ts` — update to the new shape (modify).
- `web-react/src/command.ts` — REST command client + `buildCommandUrl` (create).
- `web-react/src/command.test.ts` — URL builder + client tests (create).
- `web-react/src/dashboard/health.ts` — `deriveControlAlive`, `clampSetpoint` pure helpers (create).
- `web-react/src/dashboard/health.test.ts` — helper tests (create).
- `web-react/src/dashboard/controlButtons.ts` — rewrite to real transitions returning typed actions (modify).
- `web-react/src/dashboard/controlButtons.test.ts` — mode→buttons mapping tests (create).
- `web-react/src/dashboard/SetpointEntry.tsx` — numeric setpoint modal (create).
- `web-react/src/dashboard/ConfirmAction.tsx` — confirm modal for Stop/Shutdown (create).
- `web-react/src/dashboard/ControlButtons.tsx` — render buttons + drive modals/commands (modify).
- `web-react/src/useDashData.ts` — expose `command`, add reconnect + `controlAlive` (modify).
- `web-react/src/dashboard/Dashboard.tsx` — banners, header health, modal wiring (modify).
- `web-react/src/dashboard/Banners.tsx` — errors/warnings/critical banner strip (create).
- `web-react/tests/e2e/roundtrip.spec.ts` + `web-react/playwright.config.ts` — round-trip smoke (create).

---

## Task 1: Dev harness — `/api` proxy, launch docs, real fixture capture

**Files:**
- Modify: `web-react/vite.config.ts`
- Modify/Create: `web-react/README.md`
- Modify: `web-react/src/fixture.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `FIXTURE_DASH` (a real captured `socket_dash_data` object) that Task 2's types must satisfy; a running-backend procedure other tasks' e2e relies on.

- [ ] **Step 1: Add the `/api` proxy**

In `web-react/vite.config.ts`, extend the proxy block:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.VITE_PIFIRE_URL || "http://localhost:5000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/socket.io": { target, ws: true, changeOrigin: true },
      "/api": { target, changeOrigin: true },
    },
  },
});
```

- [ ] **Step 2: Boot the prototype backend**

From the repo root (`/home/dannyb/sources/PiFire`), in two terminals:

```bash
uv run python control.py    # control loop, prototype grill platform → datastore
uv run python app.py        # Flask+SocketIO on 0.0.0.0:5000
```

Verify: `curl -s http://localhost:5000/api/current | head -c 200` returns JSON with a `current`/`status` object. Leave both running.

- [ ] **Step 3: Capture a real `socket_dash_data` payload**

Run this one-shot capture (repo root) and save the JSON:

```bash
uv run python - <<'PY'
import json, socketio
c = socketio.Client()
got = {}
@c.on("socket_dash_data")
def _(d): got["d"] = d; c.disconnect()
c.connect("http://localhost:5000", socketio_path="/socket.io")
c.emit("listen_app_data")
c.sleep(3)
print(json.dumps(got.get("d", {}), indent=2))
PY
```

- [ ] **Step 4: Write the captured payload into `fixture.ts`**

Replace the object in `web-react/src/fixture.ts` with the captured JSON, typed `as DashData` (Task 2 defines the type; until then leave the existing import). Keep the file's top comment noting it's a real capture from the prototype backend on this date.

- [ ] **Step 5: Document the launch in README**

Add a "Running against the real backend (prototype)" section to `web-react/README.md` with the two `uv run` commands, the `curl` check, and `bun run dev` (proxied to `:5000`). Note `bun run demo` remains the offline path.

- [ ] **Step 6: Verify proxy + build**

Run (in `web-react/`):

```bash
bunx tsc -b && bun run build
```

Expected: exit 0. With the backend running and `bun run dev`, `http://localhost:5173/api/current` returns the backend JSON through the proxy.

- [ ] **Step 7: Commit**

```bash
cd web-react && git add vite.config.ts README.md src/fixture.ts
git commit -m "feat(web-react): /api proxy + prototype launch docs + real dash fixture"
```

---

## Task 2: Expand the data contract (`types.ts`)

**Files:**
- Modify: `web-react/src/types.ts`
- Modify: `web-react/src/demoData.ts`
- Test: `web-react/src/types.test.ts` (create)

**Interfaces:**
- Consumes: `FIXTURE_DASH` (Task 1).
- Produces: `DashData`, `ProbeData`, `AccentName`. Removes `SendCommand`. Later tasks import these.

- [ ] **Step 1: Write the failing test (fixture satisfies the new shape)**

Create `web-react/src/types.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { FIXTURE_DASH } from "./fixture";

describe("DashData fixture shape", () => {
  it("has the real top-level keys", () => {
    for (const k of [
      "uuid", "errors", "warnings", "criticalError", "grillName", "currentMode",
      "nextMode", "displayMode", "smokePlus", "pwmControl", "pMode", "hopperLevel",
      "lidOpenDetectEnabled", "lidOpenDetected", "tempUnits", "hasDcFan",
      "hasDistanceSensor", "allowManualOutputs", "timer", "outputs",
      "recipeStatus", "foodProbes", "primaryProbe",
    ]) {
      expect(FIXTURE_DASH).toHaveProperty(k);
    }
  });
  it("primary probe carries the rich structure", () => {
    for (const k of ["title", "temp", "setTemp", "maxTemp", "target", "targetReq", "status"]) {
      expect(FIXTURE_DASH.primaryProbe).toHaveProperty(k);
    }
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `bun run test src/types.test.ts`
Expected: FAIL (missing keys) until the fixture (Task 1) and types are aligned.

- [ ] **Step 3: Write the expanded types**

Replace `web-react/src/types.ts` with (reconcile field types against the captured fixture — if a field the backend emits differs in type, match reality):

```ts
// Mirrors blueprints/mobile/socket_io.py _get_dash_data / _get_probe_structure.
// Kept in sync with the real socket_dash_data payload; index signatures allow
// forward-compat with backend fields not modeled here.

export interface ProbeStatus {
  batteryCharging?: boolean;
  batteryPercentage?: number;
  batteryVoltage?: number;
  connected?: boolean;
  error?: boolean;
  [k: string]: unknown;
}

export interface ProbeData {
  title: string;
  label: string;
  eta: number | string;
  temp: number;
  setTemp: number;
  maxTemp: number;
  target: number;
  lowLimitTemp: number;
  highLimitTemp: number;
  targetReq: boolean;
  hasNotifications: boolean;
  lowLimitReq: boolean;
  highLimitReq: boolean;
  highLimitShutdown: boolean;
  highLimitTriggered: boolean;
  lowLimitShutdown: boolean;
  lowLimitReignite: boolean;
  lowLimitTriggered: boolean;
  targetShutdown: boolean;
  targetKeepWarm: boolean;
  device?: string;
  status: ProbeStatus;
  [k: string]: unknown;
}

export interface DashData {
  uuid: string;
  errors: string[];
  warnings: string[];
  criticalError: boolean;
  grillName: string;
  currentMode: string;
  nextMode: string;
  displayMode: string;
  smokePlus: boolean;
  pwmControl: boolean;
  pMode: number;
  hopperLevel: number;
  startupTimestamp: number;
  modeStartTime: number;
  lidOpenDetectEnabled: boolean;
  lidOpenDetected: boolean;
  lidOpenEndTime: number;
  startDuration: number;
  shutdownDuration: number;
  primeDuration: number;
  primeAmount: number;
  tempUnits: "F" | "C";
  hasDcFan: boolean;
  hasDistanceSensor: boolean;
  startupCheck: boolean;
  startToHoldPrompt: boolean;
  startupGotoTemp: number;
  startupGotoMode: string;
  allowManualOutputs: boolean;
  timer: { start: number; paused: number; end: number; keepWarm: boolean; shutdown: boolean };
  outputs: { fan: number; auger: number; igniter: number };
  recipeStatus: { recipeMode: boolean; filename: string; mode: string; paused: boolean; step: number };
  primaryProbe: ProbeData;
  foodProbes: ProbeData[];
  [k: string]: unknown;
}

export type AccentName = "ember" | "ice" | "crimson";
```

- [ ] **Step 4: Make `demoData.ts` compile against the new shape**

Update `web-react/src/demoData.ts` so its `primaryProbe`/`foodProbes` overrides spread `FIXTURE_DASH`'s probes (they now carry all required fields) and only override `temp/setTemp/target/targetReq`. No new required fields should be constructed by hand — always spread from the fixture probe.

- [ ] **Step 5: Run the full test suite**

Run: `bun run test && bunx tsc -b`
Expected: PASS (types.test + existing deriveView/demoData tests) and tsc exit 0.

- [ ] **Step 6: Commit**

```bash
cd web-react && git add src/types.ts src/demoData.ts src/types.test.ts
git commit -m "feat(web-react): align DashData/ProbeData to real socket_dash_data"
```

---

## Task 3: REST command client (`command.ts`)

**Files:**
- Create: `web-react/src/command.ts`
- Test: `web-react/src/command.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type GrillMode = "startup" | "smoke" | "shutdown" | "stop" | "monitor" | "reignite"`
  - `type SystemCmd = "reboot" | "shutdown" | "restart"`
  - `interface CommandResult { ok: boolean; message: string; data?: unknown }`
  - `interface CommandClient { setMode; hold; setSmokePlus; setPMode; prime; timerStart; timerPause; timerStop; system }` (all `(...) => Promise<CommandResult>`)
  - `function buildCommandUrl(baseUrl: string, segments: (string | number)[]): string`
  - `function createCommand(baseUrl: string): CommandClient`

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/command.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { buildCommandUrl, createCommand } from "./command";

describe("buildCommandUrl", () => {
  it("joins base + /api + segments", () => {
    expect(buildCommandUrl("", ["set", "psp", 225])).toBe("/api/set/psp/225");
    expect(buildCommandUrl("http://pi:5000", ["set", "mode", "smoke"])).toBe("http://pi:5000/api/set/mode/smoke");
  });
});

describe("createCommand issues the right URLs", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ result: "OK", message: "", data: {} }) }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const url = () => fetchMock.mock.calls[0][0];
  const opts = () => fetchMock.mock.calls[0][1];

  it("setMode → lowercase mode", async () => {
    await createCommand("").setMode("smoke");
    expect(url()).toBe("/api/set/mode/smoke");
    expect(opts().method).toBe("POST");
  });
  it("hold → psp with integer temp", async () => {
    await createCommand("").hold(225);
    expect(url()).toBe("/api/set/psp/225");
  });
  it("setSmokePlus → true/false", async () => {
    await createCommand("").setSmokePlus(true);
    expect(url()).toBe("/api/set/splus/true");
  });
  it("timerStart → seconds", async () => {
    await createCommand("").timerStart(600);
    expect(url()).toBe("/api/set/timer/start/600");
  });
  it("prime → grams and next mode", async () => {
    await createCommand("").prime(20, "smoke");
    expect(url()).toBe("/api/set/mode/prime/20/smoke");
  });
  it("system → cmd grammar", async () => {
    await createCommand("").system("reboot");
    expect(url()).toBe("/api/cmd/reboot");
  });
  it("maps a non-OK envelope to ok:false", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ result: "ERROR", message: "bad", data: {} }) });
    const r = await createCommand("").setMode("stop");
    expect(r).toEqual({ ok: false, message: "bad", data: {} });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `bun run test src/command.test.ts`
Expected: FAIL ("buildCommandUrl is not a function").

- [ ] **Step 3: Implement `command.ts`**

```ts
// REST command client using PiFire's command grammar (common/api_commands.py
// _COMMAND_DISPATCH) via blueprints/api/routes.py. Writes only; live reads come
// over the socket. Envelope: { result, message, data } (common/app.py api_response).

export type GrillMode = "startup" | "smoke" | "shutdown" | "stop" | "monitor" | "reignite";
export type SystemCmd = "reboot" | "shutdown" | "restart";

export interface CommandResult {
  ok: boolean;
  message: string;
  data?: unknown;
}

export interface CommandClient {
  setMode(mode: GrillMode): Promise<CommandResult>;
  hold(tempF: number): Promise<CommandResult>;
  setSmokePlus(on: boolean): Promise<CommandResult>;
  setPMode(n: number): Promise<CommandResult>;
  prime(grams: number, next?: GrillMode): Promise<CommandResult>;
  timerStart(seconds: number): Promise<CommandResult>;
  timerPause(): Promise<CommandResult>;
  timerStop(): Promise<CommandResult>;
  system(cmd: SystemCmd): Promise<CommandResult>;
}

export function buildCommandUrl(baseUrl: string, segments: (string | number)[]): string {
  return `${baseUrl}/api/${segments.map((s) => String(s)).join("/")}`;
}

async function post(baseUrl: string, segments: (string | number)[]): Promise<CommandResult> {
  try {
    const res = await fetch(buildCommandUrl(baseUrl, segments), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body = (await res.json()) as { result?: string; message?: string; data?: unknown };
    return { ok: body.result === "OK", message: body.message ?? "", data: body.data };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}

export function createCommand(baseUrl: string): CommandClient {
  return {
    setMode: (mode) => post(baseUrl, ["set", "mode", mode]),
    hold: (tempF) => post(baseUrl, ["set", "psp", Math.round(tempF)]),
    setSmokePlus: (on) => post(baseUrl, ["set", "splus", on ? "true" : "false"]),
    setPMode: (n) => post(baseUrl, ["set", "pmode", n]),
    prime: (grams, next) => post(baseUrl, next ? ["set", "mode", "prime", grams, next] : ["set", "mode", "prime", grams]),
    timerStart: (seconds) => post(baseUrl, ["set", "timer", "start", seconds]),
    timerPause: () => post(baseUrl, ["set", "timer", "pause"]),
    timerStop: () => post(baseUrl, ["set", "timer", "stop"]),
    system: (cmd) => post(baseUrl, ["cmd", cmd]),
  };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `bun run test src/command.test.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
cd web-react && git add src/command.ts src/command.test.ts
git commit -m "feat(web-react): typed REST command client (command grammar)"
```

---

## Task 4: Pure health helpers (`dashboard/health.ts`)

**Files:**
- Create: `web-react/src/dashboard/health.ts`
- Test: `web-react/src/dashboard/health.test.ts`

**Interfaces:**
- Consumes: `DashData` (Task 2).
- Produces:
  - `function deriveControlAlive(dash: DashData): boolean`
  - `function clampSetpoint(temp: number, units: "F" | "C"): number`
  - `const SETPOINT_RANGE: Record<"F" | "C", { min: number; max: number }>`

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/dashboard/health.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { clampSetpoint, deriveControlAlive } from "./health";
import { FIXTURE_DASH } from "../fixture";

const CONTROL_DOWN = "The control process did not respond to a request and may be stopped.";

describe("deriveControlAlive", () => {
  it("true when no control-down error present", () => {
    expect(deriveControlAlive({ ...FIXTURE_DASH, errors: [] })).toBe(true);
  });
  it("false when the control-down error is present", () => {
    expect(deriveControlAlive({ ...FIXTURE_DASH, errors: [CONTROL_DOWN] })).toBe(false);
  });
});

describe("clampSetpoint", () => {
  it("clamps to the F range", () => {
    expect(clampSetpoint(50, "F")).toBe(150);
    expect(clampSetpoint(999, "F")).toBe(500);
    expect(clampSetpoint(225, "F")).toBe(225);
  });
  it("clamps to the C range", () => {
    expect(clampSetpoint(10, "C")).toBe(65);
    expect(clampSetpoint(999, "C")).toBe(260);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `bun run test src/dashboard/health.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `health.ts`**

```ts
import type { DashData } from "../types";

// Substring of the exact error socket_io._check_control_status appends every 30s
// when the control process is unreachable.
const CONTROL_DOWN_MARKER = "control process did not respond";

export function deriveControlAlive(dash: DashData): boolean {
  return !(dash.errors ?? []).some((e) => e.includes(CONTROL_DOWN_MARKER));
}

export const SETPOINT_RANGE: Record<"F" | "C", { min: number; max: number }> = {
  F: { min: 150, max: 500 },
  C: { min: 65, max: 260 },
};

export function clampSetpoint(temp: number, units: "F" | "C"): number {
  const { min, max } = SETPOINT_RANGE[units];
  const t = Math.round(temp);
  return t < min ? min : t > max ? max : t;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `bun run test src/dashboard/health.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd web-react && git add src/dashboard/health.ts src/dashboard/health.test.ts
git commit -m "feat(web-react): control-alive + setpoint-clamp helpers"
```

---

## Task 5: Rewrite control buttons to real transitions (`controlButtons.ts`)

**Files:**
- Modify: `web-react/src/dashboard/controlButtons.ts`
- Test: `web-react/src/dashboard/controlButtons.test.ts`

**Interfaces:**
- Consumes: `DashData` (Task 2); `CommandClient` (Task 3).
- Produces:
  - `type ButtonAction = { type: "command"; run(c: CommandClient): Promise<CommandResult> } | { type: "setpoint" } | { type: "confirm"; title: string; run(c: CommandClient): Promise<CommandResult> }`
  - `interface ControlButton { label: string; variant?: "accent" | "danger"; action: ButtonAction }`
  - `function buttonsForMode(dash: DashData): ControlButton[]`

- [ ] **Step 1: Write the failing tests**

Create `web-react/src/dashboard/controlButtons.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { buttonsForMode } from "./controlButtons";
import { FIXTURE_DASH } from "../fixture";
import type { DashData } from "../types";

const at = (mode: string, over: Partial<DashData> = {}): DashData => ({ ...FIXTURE_DASH, currentMode: mode, ...over });
const labels = (d: DashData) => buttonsForMode(d).map((b) => b.label);

describe("buttonsForMode", () => {
  it("stopped → Startup / Prime / Monitor", () => {
    expect(labels(at("Stop"))).toEqual(["Startup", "Prime", "Monitor"]);
    expect(labels(at(""))).toEqual(["Startup", "Prime", "Monitor"]);
  });
  it("monitor → Startup / Stop", () => {
    expect(labels(at("Monitor"))).toEqual(["Startup", "Stop"]);
  });
  it("cooking → Smoke / Hold / Smoke+ / Shutdown / Stop", () => {
    expect(labels(at("Hold"))).toEqual(["Smoke", "Hold", "Smoke+", "Shutdown", "Stop"]);
  });
  it("Hold button opens the setpoint modal", () => {
    const hold = buttonsForMode(at("Smoke")).find((b) => b.label === "Hold")!;
    expect(hold.action.type).toBe("setpoint");
  });
  it("Stop and Shutdown are confirm actions; Smoke is a direct command", () => {
    const cooking = buttonsForMode(at("Hold"));
    expect(cooking.find((b) => b.label === "Stop")!.action.type).toBe("confirm");
    expect(cooking.find((b) => b.label === "Shutdown")!.action.type).toBe("confirm");
    expect(cooking.find((b) => b.label === "Smoke")!.action.type).toBe("command");
  });
  it("Smoke+ label reflects current state", () => {
    expect(labels(at("Hold", { smokePlus: false }))).toContain("Smoke+");
    const on = buttonsForMode(at("Hold", { smokePlus: true })).find((b) => b.label === "Smoke+")!;
    expect(on.variant).toBe("accent");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `bun run test src/dashboard/controlButtons.test.ts`
Expected: FAIL (old `buttonsForMode` returns the old shape).

- [ ] **Step 3: Rewrite `controlButtons.ts`**

```ts
import type { DashData } from "../types";
import type { CommandClient, CommandResult } from "../command";

export type ButtonAction =
  | { type: "command"; run(c: CommandClient): Promise<CommandResult> }
  | { type: "setpoint" }
  | { type: "confirm"; title: string; run(c: CommandClient): Promise<CommandResult> };

export interface ControlButton {
  label: string;
  variant?: "accent" | "danger";
  action: ButtonAction;
}

const cmd = (run: (c: CommandClient) => Promise<CommandResult>): ButtonAction => ({ type: "command", run });
const confirm = (title: string, run: (c: CommandClient) => Promise<CommandResult>): ButtonAction => ({ type: "confirm", title, run });

const STOP: ControlButton = { label: "Stop", variant: "danger", action: confirm("Stop the cook?", (c) => c.setMode("stop")) };
const STARTUP: ControlButton = { label: "Startup", variant: "accent", action: cmd((c) => c.setMode("startup")) };

export function buttonsForMode(dash: DashData): ControlButton[] {
  const mode = dash.currentMode;

  if (mode === "Stop" || mode === "Error" || mode === "") {
    return [
      STARTUP,
      { label: "Prime", action: cmd((c) => c.prime(dash.primeAmount || 10, "startup")) },
      { label: "Monitor", action: cmd((c) => c.setMode("monitor")) },
    ];
  }

  if (mode === "Monitor") {
    return [STARTUP, STOP];
  }

  // Active cook modes (Startup / Smoke / Hold / Prime / Reignite / Shutdown).
  return [
    { label: "Smoke", action: cmd((c) => c.setMode("smoke")) },
    { label: "Hold", variant: "accent", action: { type: "setpoint" } },
    { label: "Smoke+", variant: dash.smokePlus ? "accent" : undefined, action: cmd((c) => c.setSmokePlus(!dash.smokePlus)) },
    { label: "Shutdown", action: confirm("Shut down the grill?", (c) => c.setMode("shutdown")) },
    STOP,
  ];
}
```

- [ ] **Step 4: Run to verify pass**

Run: `bun run test src/dashboard/controlButtons.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd web-react && git add src/dashboard/controlButtons.ts src/dashboard/controlButtons.test.ts
git commit -m "feat(web-react): map control buttons to real mode transitions"
```

---

## Task 6: Setpoint + confirm modals

**Files:**
- Create: `web-react/src/dashboard/SetpointEntry.tsx`
- Create: `web-react/src/dashboard/ConfirmAction.tsx`
- Modify: `web-react/src/dashboard/dashboard.css` (modal styles)

**Interfaces:**
- Consumes: `clampSetpoint` (Task 4).
- Produces:
  - `function SetpointEntry(props: { open: boolean; initial: number; units: "F" | "C"; onSubmit(temp: number): void; onCancel(): void }): JSX.Element | null`
  - `function ConfirmAction(props: { open: boolean; title: string; onConfirm(): void; onCancel(): void }): JSX.Element | null`

- [ ] **Step 1: Implement `SetpointEntry.tsx`**

```tsx
import { useEffect, useState } from "react";
import { clampSetpoint, SETPOINT_RANGE } from "./health";

interface Props {
  open: boolean;
  initial: number;
  units: "F" | "C";
  onSubmit(temp: number): void;
  onCancel(): void;
}

export function SetpointEntry({ open, initial, units, onSubmit, onCancel }: Props) {
  const [temp, setTemp] = useState(initial);
  useEffect(() => { if (open) setTemp(clampSetpoint(initial, units)); }, [open, initial, units]);
  if (!open) return null;
  const step = units === "F" ? 5 : 3;
  const bump = (d: number) => setTemp((t) => clampSetpoint(t + d, units));
  const { min, max } = SETPOINT_RANGE[units];

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">Set Hold Temperature</div>
        <div className="pf-setpoint-row">
          <button className="pf-step" onClick={() => bump(-step)} aria-label="decrease">−</button>
          <div className="pf-setpoint-val">{temp}<span>°{units}</span></div>
          <button className="pf-step" onClick={() => bump(step)} aria-label="increase">+</button>
        </div>
        <input
          className="pf-setpoint-slider"
          type="range" min={min} max={max} step={step} value={temp}
          onChange={(e) => setTemp(clampSetpoint(Number(e.target.value), units))}
        />
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>Cancel</button>
          <button className="pf-modal-btn accent" onClick={() => onSubmit(temp)}>Set Hold</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Implement `ConfirmAction.tsx`**

```tsx
interface Props {
  open: boolean;
  title: string;
  onConfirm(): void;
  onCancel(): void;
}

export function ConfirmAction({ open, title, onConfirm, onCancel }: Props) {
  if (!open) return null;
  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>Cancel</button>
          <button className="pf-modal-btn danger" onClick={onConfirm}>Confirm</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add modal styles to `dashboard.css`**

Append:

```css
.pf-modal-scrim { position: absolute; inset: 0; background: rgba(0,0,0,0.6); display: grid; place-items: center; z-index: 10; }
.pf-modal { background: #2c231a; border: 1px solid rgba(255,255,255,0.14); border-radius: 18px; padding: 22px; min-width: 320px; display: flex; flex-direction: column; gap: 16px; }
.pf-modal-title { font: 700 20px "Barlow"; color: #f4ede2; text-align: center; }
.pf-setpoint-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.pf-setpoint-val { font: 800 56px "Barlow Semi Condensed"; color: #f8f2e8; font-variant-numeric: tabular-nums; }
.pf-setpoint-val span { font-size: 24px; color: #8a7f70; margin-left: 2px; }
.pf-step { width: 56px; height: 56px; border-radius: 14px; border: 2px solid var(--accent); background: color-mix(in srgb, var(--accent) 16%, transparent); color: #f4ede2; font: 700 28px "Barlow"; cursor: pointer; }
.pf-setpoint-slider { width: 100%; accent-color: var(--accent); }
.pf-modal-actions { display: flex; gap: 12px; }
.pf-modal-btn { flex: 1; border-radius: 12px; border: 1px solid rgba(255,255,255,0.14); background: #1d1813; color: #e8dfd1; font: 700 16px "Barlow"; padding: 12px; cursor: pointer; }
.pf-modal-btn.accent { background: var(--accent); color: #1a0f04; border-color: transparent; }
.pf-modal-btn.danger { background: rgba(255,90,77,0.14); border-color: #ff5a4d; color: #ff8b82; }
```

- [ ] **Step 4: Verify typecheck/build**

Run (in `web-react/`): `bunx tsc -b && bun run build`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
cd web-react && git add src/dashboard/SetpointEntry.tsx src/dashboard/ConfirmAction.tsx src/dashboard/dashboard.css
git commit -m "feat(web-react): setpoint entry + confirm modals"
```

---

## Task 7: Harden `useDashData` (command + reconnect + controlAlive)

**Files:**
- Modify: `web-react/src/useDashData.ts`

**Interfaces:**
- Consumes: `createCommand`/`CommandClient` (Task 3); `deriveControlAlive` (Task 4).
- Produces: `useDashData(): { dash: DashData; phase: ConnectionPhase; controlAlive: boolean; targetUrl: string; command: CommandClient }` where `ConnectionPhase = "connecting" | "live" | "unreachable" | "demo"`.

- [ ] **Step 1: Rewrite the hook**

```ts
import { useEffect, useMemo, useRef, useState } from "react";
import { io, type Socket } from "socket.io-client";
import type { DashData } from "./types";
import { FIXTURE_DASH } from "./fixture";
import { demoDashAt } from "./demoData";
import { createCommand, type CommandClient } from "./command";
import { deriveControlAlive } from "./dashboard/health";

export type ConnectionPhase = "connecting" | "live" | "unreachable" | "demo";

interface DashState {
  dash: DashData;
  phase: ConnectionPhase;
  controlAlive: boolean;
  targetUrl: string;
  command: CommandClient;
}

const FORCE_DEMO = import.meta.env.VITE_DEMO === "1" || import.meta.env.VITE_DEMO === "true";
const TARGET_URL = import.meta.env.VITE_PIFIRE_URL || "";

export function useDashData(): DashState {
  const [dash, setDash] = useState<DashData>(FIXTURE_DASH);
  const [phase, setPhase] = useState<ConnectionPhase>(FORCE_DEMO ? "demo" : "connecting");
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    if (FORCE_DEMO) {
      const start = Date.now();
      const tick = () => setDash(demoDashAt((Date.now() - start) / 1000));
      tick();
      const id = window.setInterval(tick, 1000);
      return () => window.clearInterval(id);
    }
    const socket = io(TARGET_URL || undefined, { path: "/socket.io", reconnection: true, timeout: 4000 });
    socketRef.current = socket;
    socket.on("connect", () => { setPhase("live"); socket.emit("listen_app_data"); });
    socket.on("connect_error", () => setPhase((p) => (p === "live" ? p : "unreachable")));
    socket.on("disconnect", () => setPhase("unreachable"));
    socket.on("socket_dash_data", (data: DashData) => { setPhase("live"); setDash(data); });
    return () => { socket.close(); socketRef.current = null; };
  }, []);

  const command = useMemo(() => createCommand(TARGET_URL), []);
  const controlAlive = phase === "demo" ? true : deriveControlAlive(dash);

  return { dash, phase, controlAlive, targetUrl: TARGET_URL || "http://localhost:5000", command };
}
```

- [ ] **Step 2: Verify typecheck + existing tests**

Run (in `web-react/`): `bunx tsc -b && bun run test`
Expected: exit 0 and all tests pass. (Type errors will appear in `App.tsx`/`Dashboard.tsx` referencing the old `send`; Task 8 fixes those — if tsc fails only in those two files, proceed to Task 8, then re-run.)

- [ ] **Step 3: Commit**

```bash
cd web-react && git add src/useDashData.ts
git commit -m "feat(web-react): hook exposes REST command + controlAlive"
```

---

## Task 8: Dashboard integration (banners, header health, modal wiring)

**Files:**
- Create: `web-react/src/dashboard/Banners.tsx`
- Modify: `web-react/src/dashboard/ControlButtons.tsx`
- Modify: `web-react/src/dashboard/Dashboard.tsx`
- Modify: `web-react/src/App.tsx`

**Interfaces:**
- Consumes: `buttonsForMode`/`ButtonAction` (Task 5), `SetpointEntry`/`ConfirmAction` (Task 6), `CommandClient` (Task 3), `useDashData` return (Task 7).
- Produces: `function Banners(props: { errors: string[]; warnings: string[]; criticalError: boolean }): JSX.Element | null`.

- [ ] **Step 1: Implement `Banners.tsx`**

```tsx
export function Banners({ errors, warnings, criticalError }: { errors: string[]; warnings: string[]; criticalError: boolean }) {
  const items = [
    ...errors.map((t) => ({ t, level: (criticalError ? "critical" : "error") as const })),
    ...warnings.map((t) => ({ t, level: "warning" as const })),
  ];
  if (items.length === 0) return null;
  return (
    <div className="pf-banners">
      {items.map((it, i) => (
        <div key={i} className={`pf-banner pf-banner--${it.level}`}>{it.t}</div>
      ))}
    </div>
  );
}
```

Add to `dashboard.css`:

```css
.pf-banners { position: absolute; top: 62px; left: 18px; right: 18px; z-index: 5; display: flex; flex-direction: column; gap: 6px; }
.pf-banner { border-radius: 10px; padding: 8px 14px; font: 600 13px "Barlow"; }
.pf-banner--warning { background: rgba(255,176,32,0.14); border: 1px solid #ffb020; color: #ffce6a; }
.pf-banner--error { background: rgba(255,90,77,0.14); border: 1px solid #ff5a4d; color: #ff8b82; }
.pf-banner--critical { background: rgba(255,90,77,0.24); border: 1px solid #ff5a4d; color: #ffd0cb; font-weight: 700; }
```

- [ ] **Step 2: Rewrite `ControlButtons.tsx` to drive commands + modals**

```tsx
import { useState } from "react";
import type { CommandClient } from "../command";
import { buttonsForMode, type ButtonAction } from "./controlButtons";
import { SetpointEntry } from "./SetpointEntry";
import { ConfirmAction } from "./ConfirmAction";
import type { DashData } from "../types";

export function ControlButtons({ dash, command, disabled }: { dash: DashData; command: CommandClient; disabled: boolean }) {
  const buttons = buttonsForMode(dash);
  const [setpointOpen, setSetpointOpen] = useState(false);
  const [confirm, setConfirm] = useState<{ title: string; run(c: CommandClient): Promise<unknown> } | null>(null);
  const [busy, setBusy] = useState(false);

  const fire = async (run: (c: CommandClient) => Promise<unknown>) => {
    setBusy(true);
    try { await run(command); } finally { setBusy(false); }
  };

  const onClick = (action: ButtonAction) => {
    if (action.type === "command") fire(action.run);
    else if (action.type === "setpoint") setSetpointOpen(true);
    else setConfirm({ title: action.title, run: action.run });
  };

  return (
    <div style={{ display: "grid", gridAutoFlow: "column", gridAutoColumns: "1fr", gap: 12, height: 82, flex: "0 0 82px" }}>
      {buttons.map((b) => {
        const danger = b.variant === "danger";
        const accent = b.variant === "accent";
        const border = danger ? "#ff5a4d" : accent ? "var(--accent)" : "rgba(255,255,255,0.14)";
        const bg = danger ? "rgba(255,90,77,0.14)" : accent ? "color-mix(in srgb, var(--accent) 16%, transparent)" : "#1d1813";
        const color = danger ? "#ff8b82" : "#e8dfd1";
        return (
          <button key={b.label} className="pf-btn" disabled={disabled || busy}
            style={{ borderColor: border, background: bg, color, opacity: disabled || busy ? 0.5 : 1 }}
            onClick={() => onClick(b.action)}>
            {b.label}
          </button>
        );
      })}

      <SetpointEntry
        open={setpointOpen}
        initial={dash.primaryProbe.setTemp || dash.primaryProbe.temp}
        units={dash.tempUnits}
        onCancel={() => setSetpointOpen(false)}
        onSubmit={(temp) => { setSetpointOpen(false); fire((c) => c.hold(temp)); }}
      />
      <ConfirmAction
        open={confirm !== null}
        title={confirm?.title ?? ""}
        onCancel={() => setConfirm(null)}
        onConfirm={() => { const run = confirm!.run; setConfirm(null); fire(run); }}
      />
    </div>
  );
}
```

- [ ] **Step 3: Update `Dashboard.tsx`**

Change the props type and the two touch points. Replace the `send` prop with `command` + `controlAlive`, render `<Banners>`, drive the header live-dot from real connection, and pass `command`/`disabled` to `ControlButtons`:

```tsx
// props interface:
interface DashboardProps {
  dash: DashData;
  command: CommandClient;
  phase: ConnectionPhase;
  controlAlive: boolean;
  accent: AccentName;
  setAccent: (a: AccentName) => void;
  animate: boolean;
  setAnimate: (v: boolean) => void;
}
```

- Import `Banners`, `CommandClient`, `ConnectionPhase`.
- Inside the stage, right after the bottom-glow div, render:
  `<Banners errors={dash.errors ?? []} warnings={dash.warnings ?? []} criticalError={dash.criticalError} />`
- Header live-dot color: `view.liveColor` stays, but add a controller-offline hint — set the DEMO/LIVE label to `phase === "demo" ? "DEMO" : controlAlive ? "LIVE" : "CTRL OFFLINE"` and color it `#ff8b82` when `!controlAlive`.
- Replace `<ControlButtons dash={dash} send={send} />` with
  `<ControlButtons dash={dash} command={command} disabled={!controlAlive} />`.

- [ ] **Step 4: Update `App.tsx`**

```tsx
import { useState } from "react";
import { useDashData } from "./useDashData";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { Dashboard } from "./dashboard/Dashboard";
import type { AccentName } from "./types";

export default function App() {
  const { dash, phase, controlAlive, targetUrl, command } = useDashData();
  const [accent, setAccent] = useState<AccentName>("ember");
  const [animate, setAnimate] = useState(true);
  document.documentElement.setAttribute("data-accent", accent);

  if (phase !== "live" && phase !== "demo") {
    return <div className="pf-fit"><ConnectionStatus phase={phase} targetUrl={targetUrl} /></div>;
  }
  return (
    <Dashboard dash={dash} command={command} phase={phase} controlAlive={controlAlive}
      accent={accent} setAccent={setAccent} animate={animate} setAnimate={setAnimate} />
  );
}
```

- [ ] **Step 5: Verify typecheck + tests + build**

Run (in `web-react/`): `bunx tsc -b && bun run test && bun run build`
Expected: exit 0; all unit tests pass.

- [ ] **Step 6: Visual smoke in demo mode**

`VITE_DEMO=1 bun run build && bunx vite preview --port 4317 --strictPort`, then confirm (screenshot or browser) the dashboard still renders and the Hold button opens the setpoint modal.

- [ ] **Step 7: Commit**

```bash
cd web-react && git add src/dashboard/Banners.tsx src/dashboard/ControlButtons.tsx src/dashboard/Dashboard.tsx src/App.tsx src/dashboard/dashboard.css
git commit -m "feat(web-react): wire dashboard to live commands, banners, health"
```

---

## Task 9: Round-trip e2e against the prototype backend

**Files:**
- Create: `web-react/playwright.config.ts`
- Create: `web-react/tests/e2e/roundtrip.spec.ts`
- Modify: `web-react/package.json` (add `test:e2e` script)

**Interfaces:**
- Consumes: the running prototype backend (Task 1) + `bun run dev`.
- Produces: an e2e proof that a command round-trips into the live socket stream.

- [ ] **Step 1: Add Playwright config**

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  use: { baseURL: "http://localhost:5173", headless: true, viewport: { width: 1280, height: 720 } },
  webServer: {
    command: "bun run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
```

- [ ] **Step 2: Add the `test:e2e` script + Playwright dep**

In `web-react/package.json` add to `scripts`: `"test:e2e": "playwright test"`, and dev-dep `@playwright/test`. Install: `bun add -d @playwright/test` (browsers already present in the main repo's env; if not, `bunx playwright install chromium`).

- [ ] **Step 3: Write the round-trip test**

```ts
import { test, expect } from "@playwright/test";

// Requires the prototype backend running: `uv run python control.py` + `uv run python app.py`.
test("startup then hold round-trips through the live socket", async ({ page }) => {
  await page.goto("/");

  // Live data renders: the grill gauge shows a numeric temperature.
  await expect(page.getByText("GRILL")).toBeVisible();

  // If currently stopped, press Startup; otherwise we're already cooking.
  const startup = page.getByRole("button", { name: "Startup" });
  if (await startup.isVisible().catch(() => false)) {
    await startup.click();
    await expect(page.getByRole("button", { name: "Hold" })).toBeVisible({ timeout: 15_000 });
  }

  // Open the Hold setpoint modal, set a temperature, submit.
  await page.getByRole("button", { name: "Hold" }).click();
  await expect(page.getByText("Set Hold Temperature")).toBeVisible();
  await page.getByRole("button", { name: "Set Hold" }).click();

  // The mode badge reflects HOLD, echoed back over the socket.
  await expect(page.getByText("HOLD")).toBeVisible({ timeout: 15_000 });
});
```

- [ ] **Step 4: Run the e2e (backend must be up)**

Run (in `web-react/`, with `control.py`+`app.py` running):

```bash
cd /home/dannyb/sources/PiFire && QT_QPA_PLATFORM=offscreen uv run python -m playwright --version >/dev/null 2>&1
cd web-react && bun run test:e2e
```

Expected: 1 passed. This is the proof "real" works end-to-end. If the mode does not change, the command path is wrong — debug the `/api/set/...` call in the network panel before proceeding.

- [ ] **Step 5: Commit**

```bash
cd web-react && git add playwright.config.ts tests/e2e/roundtrip.spec.ts package.json bun.lock
git commit -m "test(web-react): round-trip e2e against prototype backend"
```

---

## Self-Review notes (already reconciled)

- **Spec coverage:** dev harness/proxy (T1), type alignment (T2), REST command grammar (T3), controlAlive/errors (T4,T7,T8), real mode transitions incl. Hold-needs-temp (T5), setpoint+confirm UX (T6), reconnect/banners/header health (T7,T8), demo preserved (T7), unit + round-trip tests (T3,T4,T5,T9). No settings/feature pages (non-goal) — none present.
- **Type consistency:** `CommandClient`/`GrillMode`/`CommandResult` (T3) used verbatim in T5/T7/T8; `ButtonAction` union (T5) consumed in T8; `ConnectionPhase` (T7) consumed in T8; `deriveControlAlive`/`clampSetpoint`/`SETPOINT_RANGE` (T4) consumed in T6/T7.
- **Removed `SendCommand`** (T2) — all consumers (`useDashData`, `ControlButtons`, `Dashboard`, `App`) migrate to `CommandClient` in T7/T8.
