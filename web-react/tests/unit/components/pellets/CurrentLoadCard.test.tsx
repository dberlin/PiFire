import type { PelletDbSchema } from "@pifire/core/contracts/control";
import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CurrentLoadCard } from "../../../../src/components/pellets/CurrentLoadCard";

const DB: PelletDbSchema = {
  schema_version: 2,
  current: {
    pelletid: "p1",
    hopper_level: 62,
    date_loaded: "2026-07-25 09:00:00",
    est_usage: 1000,
  },
  brands: ["Generic", "Custom"],
  woods: ["Alder", "Oak"],
  archive: {
    p2: { brand: "Custom", wood: "Oak", rating: 5, comments: "second" },
    p1: { brand: "Generic", wood: "Alder", rating: 4, comments: "placeholder" },
  },
  log: { "1785013200000": { pelletid: "p1", deleted: false } },
  lastupdated: { time: 1785000000 },
};

function renderCard(over: Partial<Parameters<typeof CurrentLoadCard>[0]> = {}) {
  const props = {
    db: DB,
    hopperLevel: 62,
    tempUnits: "F" as const,
    busy: false,
    onLoadProfile: rs.fn(),
    onHopperCheck: rs.fn(),
    ...over,
  };
  render(<CurrentLoadCard {...props} />);
  return props;
}

describe("CurrentLoadCard", () => {
  it("renders the loaded profile's brand, wood, date and comments", () => {
    renderCard();
    expect(screen.getByText("Generic Alder", { exact: true })).toBeTruthy();
    expect(screen.getByText("2026-07-25 09:00:00", { exact: true })).toBeTruthy();
    expect(screen.getByText("placeholder", { exact: true })).toBeTruthy();
  });

  it("renders the rating as an accessibly-named star run, not bare glyphs", () => {
    renderCard();
    expect(screen.getByLabelText("Rating: 4 of 5")).toBeTruthy();
  });

  it("renders 'No profile loaded' when current.pelletid is missing from archive", () => {
    // The Jinja at index.html:47 would 500 here.
    const orphaned: PelletDbSchema = { ...DB, current: { ...DB.current, pelletid: "gone" } };
    renderCard({ db: orphaned });
    expect(screen.getByText("No profile loaded", { exact: true })).toBeTruthy();
  });

  it("renders the hopper level as a percentage and an aria-valued progressbar", () => {
    renderCard({ hopperLevel: 12 });
    expect(screen.getByText("12%", { exact: true })).toBeTruthy();
    const meter = screen.getByRole("progressbar");
    expect(meter.getAttribute("aria-valuenow")).toBe("12");
    expect(meter.getAttribute("aria-valuemin")).toBe("0");
    expect(meter.getAttribute("aria-valuemax")).toBe("100");
  });

  it("shows imperial usage large and metric small when tempUnits is F", () => {
    renderCard({ tempUnits: "F" });
    // est_usage 1000 g -> "2.2 lbs" / "1 kg" (helpers/pellets/usage.ts).
    expect(screen.getByLabelText("Estimated usage since reload").textContent).toBe("2.2 lbs");
    expect(screen.getByLabelText("Estimated usage since reload, alternate units").textContent).toBe(
      "1 kg",
    );
  });

  it("swaps the two usage slots when tempUnits is C", () => {
    renderCard({ tempUnits: "C" });
    expect(screen.getByLabelText("Estimated usage since reload").textContent).toBe("1 kg");
    expect(screen.getByLabelText("Estimated usage since reload, alternate units").textContent).toBe(
      "2.2 lbs",
    );
  });

  it("calls onHopperCheck once when Refresh Status is clicked", () => {
    const props = renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Refresh Status" }));
    expect(props.onHopperCheck).toHaveBeenCalledTimes(1);
  });

  it("opens a picker listing every archive profile and loads the chosen one", () => {
    const props = renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Load New Pellets" }));

    const picker = screen.getByLabelText("Profile to load");
    const labels = Array.from(picker.querySelectorAll("option")).map((o) => o.textContent);
    expect(labels).toEqual(["Generic Alder", "Custom Oak"]);

    fireEvent.change(picker, { target: { value: "p2" } });
    fireEvent.click(screen.getByRole("button", { name: "Load Profile" }));
    expect(props.onLoadProfile).toHaveBeenCalledWith("p2");
  });

  it("closes the profile picker on Escape", async () => {
    const user = userEvent.setup();
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Load New Pellets" }));
    expect(screen.getByLabelText("Profile to load")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByLabelText("Profile to load")).not.toBeInTheDocument();
  });

  it("disables both buttons while busy", () => {
    renderCard({ busy: true });
    expect(screen.getByRole("button", { name: "Refresh Status" }).hasAttribute("disabled")).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: "Load New Pellets" }).hasAttribute("disabled")).toBe(
      true,
    );
  });
});
