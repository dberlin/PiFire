jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"),
);

import { normalizeHost, rememberHost } from "../src/host";

it("adds the default scheme and port to a bare host", () => {
  expect(normalizeHost("pifire.local")).toBe("http://pifire.local:5000");
});

it("keeps an explicit scheme and port", () => {
  expect(normalizeHost("https://grill.example:8443")).toBe("https://grill.example:8443");
});

it("strips a trailing slash so the API base never doubles it", () => {
  expect(normalizeHost("http://10.0.0.5:5000/")).toBe("http://10.0.0.5:5000");
});

it("rejects input that cannot be a host", () => {
  expect(normalizeHost("   ")).toBeNull();
  expect(normalizeHost("http://")).toBeNull();
});

it("remembers most-recent-first without duplicates", async () => {
  await rememberHost("http://a.local:5000");
  await rememberHost("http://b.local:5000");
  expect(await rememberHost("http://a.local:5000")).toEqual([
    "http://a.local:5000",
    "http://b.local:5000",
  ]);
});
