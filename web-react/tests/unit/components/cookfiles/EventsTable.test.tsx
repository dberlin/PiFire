import type { CookFileEvent, CookFileTotals } from "@pifire/core/contracts/content";
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import { EventsTable } from "../../../../src/components/cookfiles/EventsTable";

afterEach(cleanup);

function event(overrides: Partial<CookFileEvent> = {}): CookFileEvent {
  return {
    id: 0,
    mode: "Smoke",
    starttime_c: "12:00:00",
    endtime_c: "13:00:00",
    augerontime_c: "0:02:00",
    estusage_m: "300 grams",
    estusage_i: "0.66 pounds (10.58 ounces)",
    pellet_level_start: 100,
    pellet_level_end: 95,
    timeinmode: "1:00:00",
    ...overrides,
  };
}

const TOTALS: CookFileTotals = {
  augerontime: "0:02:30",
  estusage_m: "375 grams",
  estusage_i: "0.83 pounds (13.23 ounces)",
  cooktime: "1:05:00",
  pellet_level_start: 100,
  pellet_level_end: 90,
};

describe("EventsTable", () => {
  it("renders one row per event with the stored display strings", () => {
    render(
      <EventsTable
        filename="X.pifire"
        events={[
          event(),
          event({ id: 1, mode: "Stop", starttime_c: "13:00:00", augerontime_c: "0:00:30" }),
        ]}
        totals={TOTALS}
        units="F"
      />,
    );
    expect(screen.getByText("Smoke")).toBeInTheDocument();
    expect(screen.getByText("Stop")).toBeInTheDocument();
    //  Stored strings, verbatim -- the CSV export reads the same rows, and a
    //  second formatter here would silently disagree with it.
    expect(screen.getByText("0:02:00")).toBeInTheDocument();
    expect(screen.getByText("0:00:30")).toBeInTheDocument();
  });

  it("shows imperial pellet usage for a Fahrenheit cook", () => {
    render(<EventsTable filename="X.pifire" events={[event()]} totals={{}} units="F" />);
    expect(screen.getByText("0.66 pounds (10.58 ounces)")).toBeInTheDocument();
    expect(screen.queryByText("300 grams")).not.toBeInTheDocument();
  });

  it("shows metric pellet usage for a Celsius cook", () => {
    //  The archive's units, not the app's: a cook recorded in F keeps showing
    //  pounds after the user switches the app to C, because the numbers in the
    //  file were never converted.
    render(<EventsTable filename="X.pifire" events={[event()]} totals={{}} units="C" />);
    expect(screen.getByText("300 grams")).toBeInTheDocument();
    expect(screen.queryByText("0.66 pounds (10.58 ounces)")).not.toBeInTheDocument();
  });

  it("renders the totals row", () => {
    render(<EventsTable filename="X.pifire" events={[event()]} totals={TOTALS} units="F" />);
    const totalsRow = screen.getByText("Totals").closest("tr") as HTMLElement;
    expect(within(totalsRow).getByText("1:05:00")).toBeInTheDocument();
    expect(within(totalsRow).getByText("0:02:30")).toBeInTheDocument();
    expect(within(totalsRow).getByText("0.83 pounds (13.23 ounces)")).toBeInTheDocument();
  });

  it("uses the cook file's units for the totals row too", () => {
    render(<EventsTable filename="X.pifire" events={[event()]} totals={TOTALS} units="C" />);
    const totalsRow = screen.getByText("Totals").closest("tr") as HTMLElement;
    expect(within(totalsRow).getByText("375 grams")).toBeInTheDocument();
  });

  it("omits the totals row when event_totals is empty", () => {
    //  The endpoint reports {} for a cook with fewer than two events rather
    //  than 500ing on prepare_event_totals' unconditional events[-2].
    render(<EventsTable filename="X.pifire" events={[event()]} totals={{}} units="F" />);
    expect(screen.queryByText("Totals")).not.toBeInTheDocument();
    expect(screen.getByText("Smoke")).toBeInTheDocument();
  });

  it("shows the no-data state when there are no events, and hides the CSV link", () => {
    render(<EventsTable filename="X.pifire" events={[]} totals={{}} units="F" />);
    expect(screen.getByText(/no recorded events/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download events CSV" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("expanding a row reveals that event's other fields", () => {
    render(
      <EventsTable
        filename="X.pifire"
        events={[event({ timeinmode: "1:00:00" })]}
        totals={{}}
        units="F"
      />,
    );
    const detail = screen.getByText("Details for Smoke at 12:00:00").closest("details");
    expect(detail).not.toBeNull();
    expect(within(detail as HTMLElement).getByText("timeinmode")).toBeInTheDocument();
    expect(within(detail as HTMLElement).getByText("1:00:00")).toBeInTheDocument();
  });

  it("does not repeat the row's own columns inside the disclosure", () => {
    render(<EventsTable filename="X.pifire" events={[event()]} totals={{}} units="F" />);
    const detail = screen.getByText("Details for Smoke at 12:00:00").closest("details");
    expect(within(detail as HTMLElement).queryByText("mode")).not.toBeInTheDocument();
    expect(within(detail as HTMLElement).queryByText("estusage_i")).not.toBeInTheDocument();
  });

  it("the CSV link points at the events export", () => {
    render(
      <EventsTable filename="Sunday Brisket.pifire" events={[event()]} totals={{}} units="F" />,
    );
    expect(screen.getByRole("link", { name: "Download events CSV" })).toHaveAttribute(
      "href",
      "/api/files/cookfiles/export?file=Sunday%20Brisket.pifire&kind=events",
    );
  });
});
