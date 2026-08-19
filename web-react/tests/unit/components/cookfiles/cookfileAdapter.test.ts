import type { CookFileChartData } from "@pifire/core/contracts/content";
import { describe, expect, test } from "@rstest/core";
import {
  hasNumericTimes,
  toChartAnnotations,
  toCookChartInput,
} from "../../../../src/components/cookfiles/cookfileAdapter";

function payload(overrides: Partial<CookFileChartData> = {}): CookFileChartData {
  return {
    time_labels: [1784942370612, 1784942373612],
    chart_data: [
      {
        label: "Grill",
        borderColor: "#f00",
        hidden: false,
        data: [
          { x: 1784942370612, y: 220 },
          { x: 1784942373612, y: 225 },
        ],
      },
    ],
    probe_mapper: { probes: { grill1: 0 }, targets: {}, primarysp: {} },
    annotations: {},
    ...overrides,
  };
}

describe("toCookChartInput", () => {
  test("adapts numeric epoch labels to the chart's epoch seconds", () => {
    const input = toCookChartInput(payload());
    expect(input?.times).toEqual([1784942370.612, 1784942373.612]);
    expect(input?.series).toHaveLength(1);
    expect(input?.series[0].label).toBe("Grill");
  });

  test("returns null for the pre-v1.5 string time labels", () => {
    //  "12:00:00" / 1000 is NaN, and uPlot renders NaN as nothing at all --
    //  a blank chart with no error anywhere. The caller reports it instead.
    expect(toCookChartInput(payload({ time_labels: ["12:00:00", "12:05:00"] }))).toBeNull();
  });

  test("returns null for an empty payload", () => {
    expect(toCookChartInput(payload({ time_labels: [], chart_data: [] }))).toBeNull();
  });

  test("returns null when every dataset is empty", () => {
    expect(
      toCookChartInput(
        payload({ chart_data: [{ label: "Grill", borderColor: "#f00", hidden: false, data: [] }] }),
      ),
    ).toBeNull();
  });

  test("drops datasets the user switched off, as the history adapter does", () => {
    const input = toCookChartInput(
      payload({
        chart_data: [
          ...payload().chart_data,
          {
            label: "Probe 1",
            borderColor: "#0f0",
            hidden: true,
            data: [
              { x: 1784942370612, y: 90 },
              { x: 1784942373612, y: 95 },
            ],
          },
        ],
      }),
    );
    expect(input?.series.map((s) => s.label)).toEqual(["Grill"]);
  });
});

describe("hasNumericTimes", () => {
  test("rejects strings, NaN and Infinity", () => {
    expect(hasNumericTimes([1, 2, 3])).toBe(true);
    expect(hasNumericTimes(["12:00:00"])).toBe(false);
    expect(hasNumericTimes([Number.NaN])).toBe(false);
    expect(hasNumericTimes([Number.POSITIVE_INFINITY])).toBe(false);
  });
});

describe("toChartAnnotations", () => {
  test("converts the event dict to a list in epoch seconds", () => {
    const out = toChartAnnotations({
      event_0: {
        type: "line",
        xMin: 1784942370612,
        xMax: 1784942370612,
        borderColor: "#abc",
        label: { content: "Smoke" },
      },
      event_1: {
        type: "line",
        xMin: 1784942373612,
        xMax: 1784942373612,
        borderColor: "#def",
      },
    });

    expect(out).toHaveLength(2);
    expect(out[0]).toMatchObject({
      xMin: 1784942370.612,
      xMax: 1784942370.612,
      borderColor: "#abc",
      label: { content: "Smoke" },
    });
    expect(out[1].borderColor).toBe("#def");
    expect(out[1].label).toBeUndefined();
  });

  test("an empty dict yields an empty list, not undefined", () => {
    expect(toChartAnnotations({})).toEqual([]);
  });
});
