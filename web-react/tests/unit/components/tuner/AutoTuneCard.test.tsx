import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutoTuneCard } from "../../../../src/components/tuner/AutoTuneCard";
import type { AutoStatus } from "../../../../src/helpers/contracts/operations.gen";

const STATUS: AutoStatus = {
  current_tr: 41000,
  current_temp: 225,
  high_tr: 0,
  high_temp: 0,
  medium_tr: 0,
  medium_temp: 0,
  low_tr: 0,
  low_temp: 0,
  samples: 3,
  ready: false,
};

const PROBES = ["Grill", "Probe1", "Probe2"];

function renderCard(over: Partial<Parameters<typeof AutoTuneCard>[0]> = {}) {
  return render(
    <AutoTuneCard
      probes={PROBES}
      reference="Probe1"
      onReferenceChange={rs.fn()}
      tuneProbe="Grill"
      status={STATUS}
      active={false}
      {...over}
    />,
  );
}

describe("AutoTuneCard", () => {
  it("offers every probe as a possible reference", () => {
    renderCard();
    const select = screen.getByRole("combobox", { name: /reference/i });
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(PROBES);
  });

  it("shows the live reference temperature and the tuned probe resistance", () => {
    renderCard();
    expect(screen.getByText("225°")).toBeVisible();
    expect(screen.getByText("41000 Ω")).toBeVisible();
  });

  it("says waiting for a null reading rather than zero", () => {
    //  null is "not reporting" -- a real 0 would read as a live measurement.
    renderCard({ status: { ...STATUS, current_temp: null, current_tr: null } });
    expect(screen.getAllByText("Waiting…").length).toBeGreaterThan(0);
    expect(screen.queryByText("0°")).toBeNull();
    expect(screen.queryByText("0 Ω")).toBeNull();
  });

  it("reports how many samples have accumulated", () => {
    renderCard({ status: { ...STATUS, samples: 7 } });
    expect(screen.getByRole("status")).toHaveTextContent(/7/);
  });

  it("announces that the spread is not yet wide enough", () => {
    renderCard({ status: { ...STATUS, ready: false } });
    expect(screen.getByRole("status")).toHaveTextContent(/collecting|not yet|keep/i);
  });

  it("announces ready once the spread is wide enough", () => {
    renderCard({ status: { ...STATUS, ready: true, samples: 14 } });
    expect(screen.getByRole("status")).toHaveTextContent(/ready/i);
  });

  it("has nothing to show before the first poll", () => {
    renderCard({ status: null });
    //  The reference selector is still there to pick before Start; the readout
    //  and progress wait for the first status.
    expect(screen.getByRole("combobox", { name: /reference/i })).toBeVisible();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("disables the reference select while a session is active", () => {
    renderCard({ active: true });
    expect(screen.getByRole("combobox", { name: /reference/i })).toBeDisabled();
  });

  it("calls onReferenceChange when a new reference is picked", async () => {
    const onReferenceChange = rs.fn();
    renderCard({ onReferenceChange });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /reference/i }), "Probe2");
    expect(onReferenceChange).toHaveBeenCalledWith("Probe2");
  });
});
