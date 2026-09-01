import { Agent } from "node:http";
import { expect, test } from "@rstest/core";
import config from "../../rsbuild.config";

test("the API proxy never reuses a backend socket past Gunicorn's keep-alive", () => {
  // Gunicorn closes idle HTTP connections after two seconds. Node's default
  // agent keeps them, so the next proxied request can race that close and get
  // one synthetic "Error occurred while trying to proxy" response. Production
  // is same-origin; this explicit dev/test proxy agent must open each backend
  // request on a fresh connection instead.
  const proxy = config.server?.proxy as Record<string, { agent?: Agent }> | undefined;
  const apiAgent = proxy?.["/api"]?.agent;

  expect(apiAgent).toBeInstanceOf(Agent);
  expect(Reflect.get(apiAgent as object, "keepAlive")).toBe(false);
});
