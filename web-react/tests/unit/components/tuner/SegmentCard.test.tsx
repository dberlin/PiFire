import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SegmentCard } from "../../../../src/components/tuner/SegmentCard";

const READING = { probe: "Grill", trohms: 51234, tuning: true };

describe("SegmentCard", () => {
  it("names its segment", () => {
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.getByRole("heading", { name: "High" })).toBeInTheDocument();
  });

  it("shows the live resistance", () => {
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.getByText("51234 Ω")).toBeVisible();
  });

  it("says the probe is not reporting rather than showing zero", () => {
    //  null is not 0. A shorted probe reads a real 0 ohms, and the operator
    //  needs to tell those apart before recording a point.
    render(
      <SegmentCard
        segment="High"
        reading={{ probe: "Grill", trohms: null, tuning: true }}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.getByText("Waiting for a reading…")).toBeVisible();
    expect(screen.queryByText("0 Ω")).toBeNull();
  });

  it("warns when the reading is stale because no session is open", () => {
    render(
      <SegmentCard
        segment="High"
        reading={{ probe: "Grill", trohms: 51234, tuning: false }}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/not updating/i);
  });

  it("records the typed temperature against the frozen resistance", async () => {
    const onRecord = rs.fn();
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={null}
        onRecord={onRecord}
        onClear={rs.fn()}
      />,
    );

    await userEvent.type(screen.getByRole("spinbutton", { name: /temperature/i }), "400");
    await userEvent.click(screen.getByRole("button", { name: "Record" }));

    expect(onRecord).toHaveBeenCalledWith(400, 51234);
  });

  it("cannot record without a temperature", () => {
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Record" })).toBeDisabled();
  });

  it("cannot record while the probe is not reporting", async () => {
    render(
      <SegmentCard
        segment="High"
        reading={{ probe: "Grill", trohms: null, tuning: true }}
        recorded={null}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    await userEvent.type(screen.getByRole("spinbutton", { name: /temperature/i }), "400");
    expect(screen.getByRole("button", { name: "Record" })).toBeDisabled();
  });

  it("shows what was recorded and offers to clear it", async () => {
    const onClear = rs.fn();
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={{ temp: 400, trohms: 51234 }}
        onRecord={rs.fn()}
        onClear={onClear}
      />,
    );
    expect(screen.getByText(/400/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onClear).toHaveBeenCalled();
  });

  it("stops offering Record once a point is recorded", () => {
    render(
      <SegmentCard
        segment="High"
        reading={READING}
        recorded={{ temp: 400, trohms: 51234 }}
        onRecord={rs.fn()}
        onClear={rs.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Record" })).toBeNull();
  });
});
