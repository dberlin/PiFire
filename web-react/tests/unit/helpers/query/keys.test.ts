import { describe, expect, it } from "@rstest/core";
import { queryKeys } from "../../../../src/helpers/query/keys";

// These tests exist to pin one relationship later tasks depend on:
// react-query invalidates by PREFIX, so useSaveSettings (Task 3) can call
// invalidateQueries({ queryKey: queryKeys.settingsRoot }) and expect it to
// reach settings, mode AND controllerMetadata in one call. If someone later
// renames one of those keys so it no longer starts with settingsRoot, the
// break is silent: the save appears to succeed while the tab revalidates
// onto stale values. Asserting the prefix STRUCTURALLY (by slicing) rather
// than re-spelling the literal arrays is what actually catches that.

describe("queryKeys settings prefix scheme", () => {
  const settingsEntries: Array<[string, readonly unknown[]]> = [
    ["settings", queryKeys.settings],
    ["mode", queryKeys.mode],
    ["controllerMetadata", queryKeys.controllerMetadata],
  ];

  it.each(settingsEntries)("settingsRoot is a true prefix of %s", (_name, key) => {
    expect(key.slice(0, queryKeys.settingsRoot.length)).toEqual(queryKeys.settingsRoot);
  });

  it("settings, mode and controllerMetadata are pairwise distinct", () => {
    expect(queryKeys.settings).not.toEqual(queryKeys.mode);
    expect(queryKeys.settings).not.toEqual(queryKeys.controllerMetadata);
    expect(queryKeys.mode).not.toEqual(queryKeys.controllerMetadata);
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
    const key = queryKeys.historyChart(minutes);
    expect(key.slice(0, queryKeys.historyRoot.length)).toEqual(queryKeys.historyRoot);
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
    expect(queryKeys.cookfileDetail(filename).slice(0, root.length)).toEqual(root);
    expect(queryKeys.cookfileChart(filename).slice(0, root.length)).toEqual(root);
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
