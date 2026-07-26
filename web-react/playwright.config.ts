import { defineConfig } from "@playwright/test";
import { ports } from "./ports";

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
      url: ports.appUrl,
      // Safe to reuse only because the port is per-checkout (see ./ports). When
      // it was the literal 5173, a second workspace's suite silently attached
      // to whichever dev server started first and reported results for a tree
      // nobody was looking at.
      reuseExistingServer: true,
      timeout: 60_000,
    },
    // Demo mode: no socket at all (helpers/useLiveState.ts), so this server is
    // independent of the shared PiFire instance the other specs mutate, and
    // demoDashAt makes the DOM shape identical on every machine. PUBLIC_DEMO is
    // read at BUILD time, which is why the fidelity/reflow projects need their
    // own server rather than a query parameter.
    {
      command: `PORT=${ports.demoPort} bun run demo`,
      url: ports.demoUrl,
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
  projects: [
    {
      name: "app",
      testIgnore:
        /dashboard-(fidelity|reflow|panel)\.spec\.ts|(pages|chrome|pellets)-fidelity\.spec\.ts/,
      use: { baseURL: ports.appUrl },
    },
    {
      name: "fidelity",
      testMatch: /dashboard-fidelity\.spec\.ts/,
      use: { baseURL: ports.demoUrl, viewport: { width: 1280, height: 720 } },
    },
    // The other half of the reflow gate. @media queries prove nothing on their
    // own: a breakpoint that declares the wrong thing, or that no element
    // consumes, is still a breakpoint.
    {
      name: "reflow",
      testMatch: /dashboard-reflow\.spec\.ts/,
      use: { baseURL: ports.demoUrl, viewport: { width: 390, height: 844 } },
    },
    // The width the grill's own screen runs at. Between the phone breakpoint
    // (max-width: 719px) and the desktop one (min-width: 1280px) sits the
    // tablet branch, and nothing drove it: the reflow shipped with its
    // 800x480 layout -- the only one an actual PiFire device renders -- backed
    // by no assertion whatsoever. 480px tall is also the shortest viewport the
    // dashboard has to survive, which no other project exercises either.
    {
      name: "panel",
      testMatch: /dashboard-panel\.spec\.ts/,
      use: { baseURL: ports.demoUrl, viewport: { width: 800, height: 480 } },
    },
    // The generalised fidelity gate. Demo server for the same reason the
    // dashboard one uses it -- useLiveState's demo branch opens no socket, so
    // these specs cannot be raced by the shared PiFire instance the rest of the
    // suite mutates -- plus stubApi(), which makes the REST-fed pages identical
    // on every machine and stops the wizard capture flushing a draft.
    //
    // The viewport is set per-describe with test.use() rather than per-project:
    // the same spec file measures both 1280x720 and 390x844, and one project per
    // viewport would double the config for nothing.
    {
      name: "fidelity-pages",
      testMatch: /pages-fidelity\.spec\.ts/,
      use: { baseURL: ports.demoUrl },
    },
    {
      name: "fidelity-chrome",
      testMatch: /chrome-fidelity\.spec\.ts/,
      use: { baseURL: ports.demoUrl },
    },
    // /pellets is socket-fed, so it cannot run on the demo server (no socket =
    // no pellet database = the "Loading..." branch) and it cannot be stubbed
    // over REST. App server, live backend, fingerprint guard. See Task 5.
    {
      name: "fidelity-pellets",
      testMatch: /pellets-fidelity\.spec\.ts/,
      use: { baseURL: ports.appUrl },
    },
  ],
});
