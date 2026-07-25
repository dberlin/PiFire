import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { type RangeProfileColumn, RangeProfileTable } from "./RangeProfileTable";

const boundaries = [60, 80, 90];

const profiles: Record<string, number>[] = [
  { startuptime: 30, augerontime: 5, p_mode: 1 },
  { startuptime: 25, augerontime: 4, p_mode: 2 },
  { startuptime: 20, augerontime: 3, p_mode: 3 },
  { startuptime: 15, augerontime: 2, p_mode: 4 },
];

const columns: RangeProfileColumn[] = [
  { key: "startuptime", label: "Startup time", suffix: "s" },
  { key: "augerontime", label: "Auger on", suffix: "s" },
  { key: "p_mode", label: "P-Mode", min: 0, max: 9 },
];

function snapshot() {
  return {
    boundaries: [...boundaries],
    profiles: profiles.map((row) => ({ ...row })),
  };
}

describe("RangeProfileTable", () => {
  it("derives range labels from boundaries", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    expect(screen.getByText("< 60°")).toBeInTheDocument();
    expect(screen.getByText("60 – 79°")).toBeInTheDocument();
    expect(screen.getByText("80 – 89°")).toBeInTheDocument();
    expect(screen.getByText("≥ 90°")).toBeInTheDocument();
  });

  it("renders rangeHeader and unit in the header, and column labels/suffixes", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="ΔT range"
        unit="°F"
        onChange={onChange}
      />,
    );
    expect(screen.getByText("ΔT range (°F)")).toBeInTheDocument();
    expect(screen.getByText("Startup time (s)")).toBeInTheDocument();
    expect(screen.getByText("Auger on (s)")).toBeInTheDocument();
    expect(screen.getByText("P-Mode")).toBeInTheDocument();
  });

  it("renders one editable boundary input per boundary, not per row", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    expect(screen.getByLabelText("Boundary 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Boundary 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Boundary 3")).toBeInTheDocument();
    // last (4th) row has no boundary to edit
    expect(screen.queryByLabelText("Boundary 4")).not.toBeInTheDocument();
  });

  it("editing a profile cell emits onChange with boundaries unchanged and only that row's key changed", () => {
    const before = snapshot();
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Auger on row 2");
    fireEvent.change(input, { target: { value: "7" } });

    expect(onChange).toHaveBeenCalledTimes(1);
    const [nextBoundaries, nextProfiles] = onChange.mock.calls[0];
    expect(nextBoundaries).toBe(boundaries);
    expect(nextProfiles).not.toBe(profiles);
    expect(nextProfiles).toHaveLength(4);
    expect(nextProfiles[0]).toBe(profiles[0]);
    expect(nextProfiles[2]).toBe(profiles[2]);
    expect(nextProfiles[3]).toBe(profiles[3]);
    expect(nextProfiles[1]).not.toBe(profiles[1]);
    expect(nextProfiles[1]).toEqual({ startuptime: 25, augerontime: 7, p_mode: 2 });

    // props untouched
    expect(boundaries).toEqual(before.boundaries);
    expect(profiles).toEqual(before.profiles);
  });

  it("editing a boundary input emits onChange with only that boundary changed, profiles untouched", () => {
    const before = snapshot();
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Boundary 1");
    fireEvent.change(input, { target: { value: "65" } });

    expect(onChange).toHaveBeenCalledTimes(1);
    const [nextBoundaries, nextProfiles] = onChange.mock.calls[0];
    expect(nextBoundaries).not.toBe(boundaries);
    expect(nextBoundaries).toEqual([65, 80, 90]);
    expect(nextProfiles).toBe(profiles);

    // props untouched
    expect(boundaries).toEqual(before.boundaries);
    expect(profiles).toEqual(before.profiles);
  });

  it("+ Add appends a boundary (last + 10) and a copy of the last profile row, keeping the invariant", () => {
    const before = snapshot();
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "+ Add" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const [nextBoundaries, nextProfiles] = onChange.mock.calls[0];
    expect(nextBoundaries).toEqual([60, 80, 90, 100]);
    expect(nextProfiles).toEqual([...profiles, { startuptime: 15, augerontime: 2, p_mode: 4 }]);
    expect(nextProfiles[4]).not.toBe(profiles[3]);
    expect(nextProfiles.length).toBe(nextBoundaries.length + 1);

    // props untouched
    expect(boundaries).toEqual(before.boundaries);
    expect(profiles).toEqual(before.profiles);
  });

  it("removing a middle row drops boundaries[i] and profiles[i], keeping the invariant", () => {
    const before = snapshot();
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByLabelText("Remove row 2"));

    expect(onChange).toHaveBeenCalledTimes(1);
    const [nextBoundaries, nextProfiles] = onChange.mock.calls[0];
    expect(nextBoundaries).toEqual([60, 90]);
    expect(nextProfiles).toEqual([
      { startuptime: 30, augerontime: 5, p_mode: 1 },
      { startuptime: 20, augerontime: 3, p_mode: 3 },
      { startuptime: 15, augerontime: 2, p_mode: 4 },
    ]);
    expect(nextProfiles.length).toBe(nextBoundaries.length + 1);

    expect(boundaries).toEqual(before.boundaries);
    expect(profiles).toEqual(before.profiles);
  });

  it("removing the last row drops the last boundary (min(i, N-1)), keeping the invariant", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByLabelText("Remove row 4"));

    expect(onChange).toHaveBeenCalledTimes(1);
    const [nextBoundaries, nextProfiles] = onChange.mock.calls[0];
    expect(nextBoundaries).toEqual([60, 80]);
    expect(nextProfiles).toEqual([
      { startuptime: 30, augerontime: 5, p_mode: 1 },
      { startuptime: 25, augerontime: 4, p_mode: 2 },
      { startuptime: 20, augerontime: 3, p_mode: 3 },
    ]);
    expect(nextProfiles.length).toBe(nextBoundaries.length + 1);
  });

  it("disables remove buttons at the 2-row / 1-boundary floor", () => {
    const onChange = rs.fn();
    const floorBoundaries = [70];
    const floorProfiles: Record<string, number>[] = [
      { startuptime: 30, augerontime: 5, p_mode: 1 },
      { startuptime: 15, augerontime: 2, p_mode: 4 },
    ];
    render(
      <RangeProfileTable
        boundaries={floorBoundaries}
        profiles={floorProfiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    expect(screen.getByLabelText("Remove row 1")).toBeDisabled();
    expect(screen.getByLabelText("Remove row 2")).toBeDisabled();

    fireEvent.click(screen.getByLabelText("Remove row 1"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("clamps a cell value to the column's max on change", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("P-Mode row 1");
    fireEvent.change(input, { target: { value: "999" } });

    expect(onChange).toHaveBeenCalledTimes(1);
    const [, nextProfiles] = onChange.mock.calls[0];
    expect(nextProfiles[0].p_mode).toBe(9);
  });

  it("clamps a cell value to the column's min on change", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("P-Mode row 1");
    fireEvent.change(input, { target: { value: "-5" } });

    expect(onChange).toHaveBeenCalledTimes(1);
    const [, nextProfiles] = onChange.mock.calls[0];
    expect(nextProfiles[0].p_mode).toBe(0);
  });

  it("does not clamp a column with no min/max", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={boundaries}
        profiles={profiles}
        columns={columns}
        rangeHeader="Range"
        unit="°F"
        onChange={onChange}
      />,
    );
    const input = screen.getByLabelText("Startup time row 1");
    fireEvent.change(input, { target: { value: "999" } });

    expect(onChange).toHaveBeenCalledTimes(1);
    const [, nextProfiles] = onChange.mock.calls[0];
    expect(nextProfiles[0].startuptime).toBe(999);
  });

  it("shows an 'All' range label when there are no boundaries", () => {
    const onChange = rs.fn();
    render(
      <RangeProfileTable
        boundaries={[]}
        profiles={[{ duty_cycle: 50 }]}
        columns={[{ key: "duty_cycle", label: "Duty cycle", suffix: "%", min: 0, max: 100 }]}
        rangeHeader="ΔT range"
        unit="°F"
        onChange={onChange}
      />,
    );
    expect(screen.getByText("All")).toBeInTheDocument();
    expect(screen.queryByLabelText("Boundary 1")).not.toBeInTheDocument();
  });
  // I18: the schema does NOT catch an unsorted temp_range_list
  // (SmartStart._check_profile_count / PwmSettings._check_profiles check counts
  // and per-row bounds, never ordering), so an inverted list SAVES and then
  // controller/runtime/logic/smartstart.py:8-12 silently strands every profile
  // after the inversion. Flask enforces strict ordering in both paths
  // (settings.js:195-244 saveChanges, :341-380 onAdd).
  describe("monotonic boundaries", () => {
    const renderTable = (onChange: ReturnType<typeof rs.fn>, bounds: number[] = boundaries) =>
      render(
        <RangeProfileTable
          boundaries={bounds}
          profiles={profiles}
          columns={columns}
          rangeHeader="Range"
          unit="°F"
          onChange={onChange}
          boundaryMin={0}
          boundaryMax={200}
        />,
      );

    it("refuses a boundary past its successor and names the permitted range", () => {
      const onChange = rs.fn();
      renderTable(onChange);

      // [60, 80, 90]: boundary 2 must stay strictly between 60 and 80.
      fireEvent.change(screen.getByLabelText("Boundary 2"), { target: { value: "95" } });

      // Never emitted as an unsorted array.
      expect(onChange).not.toHaveBeenCalled();
      expect(screen.getByRole("alert")).toHaveTextContent("61");
      expect(screen.getByRole("alert")).toHaveTextContent("89");
      // The typed text is still visible so multi-digit entry stays possible.
      expect(screen.getByLabelText("Boundary 2")).toHaveValue(95);
    });

    it("accepts a boundary strictly between its neighbours", () => {
      const onChange = rs.fn();
      renderTable(onChange);

      fireEvent.change(screen.getByLabelText("Boundary 2"), { target: { value: "70" } });

      expect(onChange).toHaveBeenCalledTimes(1);
      const [next] = onChange.mock.calls[0];
      expect(next).toEqual([60, 70, 90]);
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("clamps an out-of-range boundary on blur, not on change", () => {
      const onChange = rs.fn();
      renderTable(onChange);

      const input = screen.getByLabelText("Boundary 1");
      fireEvent.change(input, { target: { value: "-5" } });
      expect(onChange).not.toHaveBeenCalled();

      fireEvent.blur(input);
      expect(onChange).toHaveBeenCalledTimes(1);
      const [next] = onChange.mock.calls[0];
      expect(next).toEqual([0, 80, 90]); // clamped up to boundaryMin
    });

    it("bounds the last boundary below by its predecessor and above by boundaryMax", () => {
      const onChange = rs.fn();
      renderTable(onChange);

      const last = screen.getByLabelText("Boundary 3");
      expect(last).toHaveAttribute("min", "81");
      expect(last).toHaveAttribute("max", "200");

      fireEvent.change(last, { target: { value: "999" } });
      fireEvent.blur(last);
      expect(onChange.mock.calls[0][0]).toEqual([60, 80, 200]);
    });

    it("carries per-index min/max DOM attributes derived from the neighbours", () => {
      renderTable(rs.fn());

      expect(screen.getByLabelText("Boundary 1")).toHaveAttribute("min", "0");
      expect(screen.getByLabelText("Boundary 1")).toHaveAttribute("max", "79");
      expect(screen.getByLabelText("Boundary 2")).toHaveAttribute("min", "61");
      expect(screen.getByLabelText("Boundary 2")).toHaveAttribute("max", "89");
    });

    it("omits the boundary min/max attributes when the props are not supplied", () => {
      render(
        <RangeProfileTable
          boundaries={[60]}
          profiles={[profiles[0], profiles[1]]}
          columns={columns}
          rangeHeader="Range"
          unit="°F"
          onChange={rs.fn()}
        />,
      );
      const only = screen.getByLabelText("Boundary 1");
      expect(only).not.toHaveAttribute("min");
      expect(only).not.toHaveAttribute("max");
    });

    it("+ Add still appends last + 10, preserving order", () => {
      const onChange = rs.fn();
      renderTable(onChange);

      fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
      const [next] = onChange.mock.calls[0];
      expect(next).toEqual([60, 80, 90, 100]);
      expect([...next].sort((a: number, b: number) => a - b)).toEqual(next);
    });

    it("still refuses to remove below 2 profiles", () => {
      const onChange = rs.fn();
      render(
        <RangeProfileTable
          boundaries={[70]}
          profiles={[profiles[0], profiles[1]]}
          columns={columns}
          rangeHeader="Range"
          unit="°F"
          onChange={onChange}
          boundaryMin={0}
          boundaryMax={200}
        />,
      );
      expect(screen.getByLabelText("Remove row 1")).toBeDisabled();
      fireEvent.click(screen.getByLabelText("Remove row 1"));
      expect(onChange).not.toHaveBeenCalled();
    });
  });
});
