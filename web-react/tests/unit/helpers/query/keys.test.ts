import { describe, expect, it } from "@rstest/core";

import { queryKeys } from "../../../../src/helpers/query/keys";

// These tests pin the per-origin relationship that settings invalidation
// depends on: react-query invalidates by PREFIX, so useSaveSettings can call
// invalidateQueries({ queryKey: queryKeys.settingsRoot(baseUrl) }) and expect
// it to reach settings, mode AND controllerMetadata for that origin. If
// someone later renames one of those keys so it no longer starts with the
// matching settingsRoot(baseUrl), the
// break is silent: the save appears to succeed while the tab revalidates
// onto stale values. Asserting the prefix STRUCTURALLY (by slicing) rather
// than re-spelling the literal arrays is what actually catches that.

/**
 * Assert `root` is a TRUE prefix of `key` -- the whole claim, not just the
 * slice comparison.
 *
 * `key.slice(0, root.length)` on its own is vacuous at both ends: an emptied
 * root passes against every key in existence (`[].slice(0, 0)` equals `[]`),
 * and a root equal to the key passes too. Both are exactly the mistakes these
 * tests are here to catch -- an empty root would make
 * invalidateQueries({ queryKey: root }) match the ENTIRE cache rather than one
 * family, and a root identical to a leaf would reach only itself. So the
 * length relationship is asserted first, and the slice second.
 */
function expectTruePrefix(root: readonly unknown[], key: readonly unknown[]) {
  expect(root.length).toBeGreaterThan(0);
  expect(key.length).toBeGreaterThan(root.length);
  expect(key.slice(0, root.length)).toEqual(root);
}

describe("queryKeys settings prefix scheme", () => {
  const baseUrl = "http://pifire.local:5000";
  const settingsEntries: Array<[string, readonly unknown[]]> = [
    ["settings", queryKeys.settings(baseUrl)],
    ["mode", queryKeys.mode(baseUrl)],
    ["controllerMetadata", queryKeys.controllerMetadata(baseUrl)],
  ];

  it.each(settingsEntries)("settingsRoot is a true prefix of %s", (_name, key) => {
    expectTruePrefix(queryKeys.settingsRoot(baseUrl), key);
  });

  it("settings, mode and controllerMetadata are pairwise distinct", () => {
    expect(queryKeys.settings(baseUrl)).not.toEqual(queryKeys.mode(baseUrl));
    expect(queryKeys.settings(baseUrl)).not.toEqual(queryKeys.controllerMetadata(baseUrl));
    expect(queryKeys.mode(baseUrl)).not.toEqual(queryKeys.controllerMetadata(baseUrl));
  });
});

describe("queryKeys.historyChart", () => {
  it("returns equal keys for equal arguments", () => {
    expect(queryKeys.historyChart(60)).toEqual(queryKeys.historyChart(60));
  });

  it("returns distinct keys for distinct arguments", () => {
    expect(queryKeys.historyChart(1)).not.toEqual(queryKeys.historyChart(60));
  });

  it("maps undefined to a stable null segment, distinct from a real minute value", () => {
    expect(queryKeys.historyChart(undefined)).toEqual(["history", "chart", null]);
    expect(queryKeys.historyChart(undefined)).toEqual(queryKeys.historyChart(undefined));
    expect(queryKeys.historyChart(undefined)).not.toEqual(queryKeys.historyChart(1));
  });

  it.each([[60], [undefined]])("historyRoot is a true prefix of historyChart(%s)", (minutes) => {
    expectTruePrefix(queryKeys.historyRoot, queryKeys.historyChart(minutes));
  });
});

describe("queryKeys.cookfileDetail / cookfileChart", () => {
  it("returns equal keys for equal filenames", () => {
    expect(queryKeys.cookfileDetail("cook-1.json")).toEqual(
      queryKeys.cookfileDetail("cook-1.json"),
    );
    expect(queryKeys.cookfileChart("cook-1.json")).toEqual(queryKeys.cookfileChart("cook-1.json"));
  });

  it("returns distinct keys for distinct filenames", () => {
    expect(queryKeys.cookfileDetail("cook-1.json")).not.toEqual(
      queryKeys.cookfileDetail("cook-2.json"),
    );
    expect(queryKeys.cookfileChart("cook-1.json")).not.toEqual(
      queryKeys.cookfileChart("cook-2.json"),
    );
  });

  it("cookfileRoot is a true prefix of both cookfileDetail and cookfileChart for the same filename", () => {
    const filename = "cook-1.json";
    const root = queryKeys.cookfileRoot(filename);
    expectTruePrefix(root, queryKeys.cookfileDetail(filename));
    expectTruePrefix(root, queryKeys.cookfileChart(filename));
  });
});

describe("queryKeys.recipe", () => {
  it("returns equal keys for equal filenames", () => {
    expect(queryKeys.recipe("brisket.json")).toEqual(queryKeys.recipe("brisket.json"));
  });

  it("returns distinct keys for distinct filenames", () => {
    expect(queryKeys.recipe("brisket.json")).not.toEqual(queryKeys.recipe("ribs.json"));
  });
});
