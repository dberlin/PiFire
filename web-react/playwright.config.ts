import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  // Every spec drives ONE shared, stateful PiFire instance (control.py plus
  // its SQLite datastore), so these tests are not isolated from each other the
  // way ordinary unit tests are. Playwright runs separate spec FILES in
  // parallel workers by default, and several of the things this suite does are
  // globally destructive to that shared instance:
  //
  //   - a units change (settings.spec.ts) and entering Startup mode
  //     (roundtrip.spec.ts) each make control.py run
  //     `read_history(0, flushhistory=True)`, wiping the ENTIRE history store
  //     -- including the rows history.spec.ts seeds to get a chart to render;
  //   - `ensureStopped` and the mode buttons move a single global grill mode,
  //     so one file's Stop cancels another file's cook mid-assertion.
  //
  // Serialising the suite is what actually makes it deterministic; before
  // this, roundtrip.spec.ts already failed intermittently on exactly that
  // shared-mode race. The cost is roughly ten seconds of wall clock.
  workers: 1,
  use: { headless: true, viewport: { width: 1280, height: 720 } },
  webServer: [
    {
      command: "bun run dev",
      url: "http://localhost:5173",
      reuseExistingServer: true,
      timeout: 60_000,
    },
    // Demo mode: no socket at all (helpers/useLiveState.ts), so this server is
    // independent of the shared PiFire instance the other specs mutate, and
    // demoDashAt makes the DOM shape identical on every machine. PUBLIC_DEMO is
    // read at BUILD time, which is why the fidelity/reflow projects need their
    // own server rather than a query parameter.
    {
      command: "PORT=5174 bun run demo",
      url: "http://localhost:5174",
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
  projects: [
    {
      name: "app",
      testIgnore: /dashboard-(fidelity|reflow)\.spec\.ts/,
      use: { baseURL: "http://localhost:5173" },
    },
    {
      name: "fidelity",
      testMatch: /dashboard-fidelity\.spec\.ts/,
      use: { baseURL: "http://localhost:5174", viewport: { width: 1280, height: 720 } },
    },
    // The other half of the reflow gate. @media queries prove nothing on their
    // own: a breakpoint that declares the wrong thing, or that no element
    // consumes, is still a breakpoint.
    {
      name: "reflow",
      testMatch: /dashboard-reflow\.spec\.ts/,
      use: { baseURL: "http://localhost:5174", viewport: { width: 390, height: 844 } },
    },
  ],
});
