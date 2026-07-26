import { expect, test } from "@rstest/core";
import { resolvePorts } from "./ports";

test("an empty environment reproduces the single-checkout defaults", () => {
  const p = resolvePorts({});
  expect(p.appPort).toBe(5173);
  expect(p.demoPort).toBe(5174);
  expect(p.appUrl).toBe("http://localhost:5173");
  expect(p.demoUrl).toBe("http://localhost:5174");
  expect(p.pifireUrl).toBe("http://localhost:5000");
});

test("a second workspace gets an entirely disjoint set of origins", () => {
  const p = resolvePorts({
    PORT: "5273",
    DEMO_PORT: "5274",
    PIFIRE_BACKEND_URL: "http://localhost:5100",
  });
  expect(p.appUrl).toBe("http://localhost:5273");
  expect(p.demoUrl).toBe("http://localhost:5274");
  expect(p.pifireUrl).toBe("http://localhost:5100");
});

test("the backend URL is taken from a variable rsbuild will NOT inject into the bundle", () => {
  // The regression this pins: `PUBLIC_*` names are injected into the browser
  // bundle by rsbuild, and eight modules read `import.meta.env.PUBLIC_PIFIRE_URL`
  // as their fetch base. Naming the workspace variable `PUBLIC_PIFIRE_URL` made
  // every request absolute and cross-origin, bypassing the dev proxy; Flask
  // sends no CORS headers, so the browser blocked them and every loader threw.
  // A workspace must be able to aim the PROXY somewhere without moving the
  // BROWSER's origin.
  const p = resolvePorts({ PIFIRE_BACKEND_URL: "http://localhost:5100" });
  expect(p.pifireUrl).toBe("http://localhost:5100");
  expect("PIFIRE_BACKEND_URL".startsWith("PUBLIC_")).toBe(false);
});

test("PUBLIC_PIFIRE_URL still works alone, for pointing a single checkout at a real grill", () => {
  // There the browser and the proxy SHOULD agree on one absolute origin.
  expect(resolvePorts({ PUBLIC_PIFIRE_URL: "http://grill.local:5000" }).pifireUrl).toBe(
    "http://grill.local:5000",
  );
});

test("PIFIRE_BACKEND_URL wins when both are set", () => {
  const p = resolvePorts({
    PIFIRE_BACKEND_URL: "http://localhost:5100",
    PUBLIC_PIFIRE_URL: "http://grill.local:5000",
  });
  expect(p.pifireUrl).toBe("http://localhost:5100");
});

test("an unset or empty variable falls back instead of binding port 0", () => {
  // Number("") === 0, which is a real port meaning "any free port" -- a server
  // on it is unreachable at the URL we would have advertised.
  expect(resolvePorts({ PORT: "" }).appPort).toBe(5173);
  expect(resolvePorts({ PORT: undefined }).appPort).toBe(5173);
  expect(resolvePorts({ DEMO_PORT: "" }).demoPort).toBe(5174);
});

test("a trailing slash on the backend URL does not survive into request paths", () => {
  // Callers append "/api/..."; without this, requests go to //api/... which
  // some servers treat as a different path.
  expect(resolvePorts({ PUBLIC_PIFIRE_URL: "http://localhost:5100/" }).pifireUrl).toBe(
    "http://localhost:5100",
  );
});

test("the app and demo servers never collide by default", () => {
  const p = resolvePorts({});
  expect(p.appPort).not.toBe(p.demoPort);
});

test("a unit test run never sees the shell's PUBLIC_* backend origins", () => {
  // The seam this pins: `ports.ts` reads process.env, but the app modules that
  // pick a backend origin read `import.meta.env.PUBLIC_PIFIRE_URL`, which
  // rsbuild INLINES from the ambient shell at build time -- and useLiveState
  // falls back through PUBLIC_PIFIRE_TARGET as well. With either exported,
  // useLiveState.test.tsx stops exercising the in-code fallback and asserts
  // against the developer's shell instead. Measured before rstest.config.ts
  // defined them away: "expected http://localhost:5000, received
  // http://localhost:5300".
  //
  // PIFIRE_BACKEND_URL, which is what a workspace sets today, is intentionally
  // not a PUBLIC_ name and so never reaches the bundle. These two still do,
  // and both remain supported for pointing one checkout at a real grill.
  expect(import.meta.env.PUBLIC_PIFIRE_URL).toBe("");
  expect(import.meta.env.PUBLIC_PIFIRE_TARGET).toBe("");
});
