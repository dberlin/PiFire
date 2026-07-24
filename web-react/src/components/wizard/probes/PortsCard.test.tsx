import { afterEach, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ProbeMap, ProbeProfile } from "../../../helpers/wizard/probeTypes";
import { PortsCard } from "./PortsCard";

afterEach(cleanup);

const profiles: ProbeProfile[] = [{ A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" }];
const pmWith = (info: ProbeMap["probe_info"]): ProbeMap => ({
  probe_devices: [
    {
      device: "ADS1115",
      module: "ads1115_adafruit",
      module_filename: "ads1115_adafruit",
      ports: ["ADC0", "ADC1"],
      config: {},
    },
  ],
  probe_info: info,
});

it("lists probes and shows profile name only for ADC ports", () => {
  const pm = pmWith([
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: { A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" },
    },
    {
      name: "Avg",
      label: "Avg",
      type: "Aux",
      enabled: true,
      device: "Avg",
      port: "VIRT0",
      profile: {},
    },
  ]);
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={rs.fn()} />);
  expect(screen.getByText("PT-1000")).toBeInTheDocument();
  expect(screen.getByText("NA")).toBeInTheDocument();
});

it("deleting the only Primary while a probe remains surfaces the guard error", () => {
  const pm = pmWith([
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: {},
    },
    {
      name: "Food",
      label: "Food",
      type: "Food",
      enabled: true,
      device: "ADS1115",
      port: "ADC1",
      profile: {},
    },
  ]);
  const onChange = rs.fn();
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={onChange} />);
  fireEvent.click(screen.getAllByRole("button", { name: /delete/i })[0]); // delete Grill (Primary)
  expect(screen.getByRole("alert")).toHaveTextContent(/Primary/i);
  expect(onChange).not.toHaveBeenCalled();
});

it("editing a probe emits the updated map", () => {
  const pm = pmWith([
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: {},
    },
  ]);
  const onChange = rs.fn();
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.change(screen.getByLabelText(/probe name/i), { target: { value: "GrillTop" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$|save/i }));
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({
      probe_info: expect.arrayContaining([expect.objectContaining({ label: "GrillTop" })]),
    }),
  );
});

it("cancelling the form clears it without emitting", () => {
  const pm = pmWith([
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: {},
    },
  ]);
  const onChange = rs.fn();
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
});

it("an add validation error surfaces inside the form dialog without emitting", () => {
  const pm = pmWith([
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: {},
    },
  ]);
  const onChange = rs.fn();
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /add probe/i }));
  // Leave name blank -- addProbe rejects an empty name.
  fireEvent.click(screen.getByRole("button", { name: /^add$|save/i }));
  expect(screen.getByRole("alert")).toHaveTextContent(/name/i);
  expect(onChange).not.toHaveBeenCalled();
});

it("disables row Edit/Delete buttons while the Add-Probe form is open", () => {
  const pm = pmWith([
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: {},
    },
  ]);
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={rs.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /add probe/i }));
  expect(screen.getByRole("button", { name: /^edit$/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
});

it("disables row Edit/Delete buttons while the edit form is open", () => {
  const pm = pmWith([
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: {},
    },
  ]);
  render(<PortsCard probeMap={pm} profiles={profiles} onChange={rs.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(screen.getByRole("button", { name: /^edit$/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
});

it("adding a probe emits the new map", () => {
  const onChange = rs.fn();
  render(<PortsCard probeMap={pmWith([])} profiles={profiles} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /add probe/i }));
  fireEvent.change(screen.getByLabelText(/probe name/i), { target: { value: "Grill" } });
  fireEvent.change(screen.getByLabelText(/device & port/i), { target: { value: "ADS1115:ADC0" } });
  fireEvent.change(screen.getByLabelText(/probe type/i), { target: { value: "Primary" } });
  fireEvent.click(screen.getByRole("button", { name: /^add$|save/i }));
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({
      probe_info: expect.arrayContaining([
        expect.objectContaining({ label: "Grill", device: "ADS1115", port: "ADC0" }),
      ]),
    }),
  );
});
