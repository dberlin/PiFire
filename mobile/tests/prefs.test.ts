jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"),
);

import AsyncStorage from "@react-native-async-storage/async-storage";
import { defaultPrefs, loadPrefs, mergePrefs, savePrefs } from "../src/prefs";

// The mock's storage is a module-level singleton shared across every test in
// this file (see host.test.ts's "remembers most-recent-first" test, which
// relies on that same persistence within its own test) -- clear it first so
// this file's tests don't observe each other's writes.
beforeEach(async () => {
  await AsyncStorage.clear();
});

it("falls back to defaults for anything missing or unknown", () => {
  expect(mergePrefs({ accent: "nonsense" })).toEqual(defaultPrefs);
});

it("keeps a valid stored accent", () => {
  expect(mergePrefs({ accent: "ice" }).accent).toBe("ice");
});

it("falls back to defaults for a non-object", () => {
  expect(mergePrefs(null)).toEqual(defaultPrefs);
  expect(mergePrefs("nonsense")).toEqual(defaultPrefs);
  expect(mergePrefs(undefined)).toEqual(defaultPrefs);
});

it("keeps a valid alerts flag and drops a malformed one", () => {
  expect(mergePrefs({ alerts: false }).alerts).toBe(false);
  expect(mergePrefs({ alerts: "nope" }).alerts).toBe(defaultPrefs.alerts);
});

it("round-trips accent and alerts through AsyncStorage", async () => {
  await savePrefs({ host: "http://pifire.local", accent: "crimson", alerts: false });
  const loaded = await loadPrefs();
  expect(loaded.accent).toBe("crimson");
  expect(loaded.alerts).toBe(false);
});

it("loadPrefs falls back to defaults when nothing is stored", async () => {
  const loaded = await loadPrefs();
  expect(loaded.accent).toBe(defaultPrefs.accent);
  expect(loaded.alerts).toBe(defaultPrefs.alerts);
});
