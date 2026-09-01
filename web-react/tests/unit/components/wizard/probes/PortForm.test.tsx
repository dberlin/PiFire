import type { ProbeProfile } from "@pifire/core/contracts/wizard";
import { afterEach, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { PortForm } from "../../../../../src/components/wizard/probes/PortForm";

afterEach(cleanup);

const profiles: ProbeProfile[] = [{ A: 1, B: 2, C: 3, id: "PT-1000", name: "PT-1000" }];
const devicePortOptions = [
  { value: "ADS1115:ADC0", label: "ADS1115 -> ADC0" },
  { value: "Avg:VIRT0", label: "Avg -> VIRT0" },
];

it("hides the Profile row when device_port is a VIRT value", () => {
  render(
    <PortForm
      mode="add"
      devicePortOptions={devicePortOptions}
      profiles={profiles}
      values={{ name: "", device_port: "Avg:VIRT0", type: "Food", profile_id: "", enabled: "true" }}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );
  expect(screen.queryByText(/probe profile/i)).not.toBeInTheDocument();
});

it("shows the Profile row when device_port is an ADC value", () => {
  render(
    <PortForm
      mode="add"
      devicePortOptions={devicePortOptions}
      profiles={profiles}
      values={{
        name: "",
        device_port: "ADS1115:ADC0",
        type: "Food",
        profile_id: "",
        enabled: "true",
      }}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );
  expect(screen.getByText(/^probe profile$/i)).toBeInTheDocument();
});

it("hides the Enabled row when type is Aux", () => {
  render(
    <PortForm
      mode="add"
      devicePortOptions={devicePortOptions}
      profiles={profiles}
      values={{ name: "", device_port: "Avg:VIRT0", type: "Aux", profile_id: "", enabled: "true" }}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );
  expect(screen.queryByText(/^enabled$/i)).not.toBeInTheDocument();
});

it("renders the Probe Type field description as real HTML", () => {
  render(
    <PortForm
      mode="add"
      devicePortOptions={devicePortOptions}
      profiles={profiles}
      values={{ name: "", device_port: "Avg:VIRT0", type: "Food", profile_id: "", enabled: "true" }}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );
  expect(document.querySelector(".pf-field-hint strong")).not.toBeNull();
});

it("shows the Enabled row when type is not Aux", () => {
  render(
    <PortForm
      mode="add"
      devicePortOptions={devicePortOptions}
      profiles={profiles}
      values={{ name: "", device_port: "Avg:VIRT0", type: "Food", profile_id: "", enabled: "true" }}
      onFieldChange={rs.fn()}
      onSubmit={rs.fn()}
      onCancel={rs.fn()}
      error={null}
    />,
  );
  expect(screen.getByText(/^enabled$/i)).toBeInTheDocument();
});
