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
    PUBLIC_PIFIRE_URL: "http://localhost:5100",
  });
  expect(p.appUrl).toBe("http://localhost:5273");
  expect(p.demoUrl).toBe("http://localhost:5274");
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
