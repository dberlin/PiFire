import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ProbeMap, ProbeModuleData } from "../../../helpers/wizard/probeTypes";
import { DevicesCard } from "./DevicesCard";

rs.mock("../../../helpers/wizard/wizardApi", () => ({
  validateBusKinds: rs.fn(async () => ({ ok: true })),
}));

import { validateBusKinds } from "../../../helpers/wizard/wizardApi";

beforeEach(() => {
  (validateBusKinds as ReturnType<typeof rs.fn>).mockClear();
});

afterEach(cleanup);

const modules: Record<string, ProbeModuleData> = {
  ads1115_adafruit: {
    friendly_name: "ADS1115 Adafruit",
    filename: "ads1115_adafruit",
    image: "ads1115.png",
    device_specific: {
      ports: ["ADC0", "ADC1"],
      type: "adc",
      config: [
        {
          label: "i2c_bus_addr",
          friendly_name: "I2C Bus Address",
          type: "list",
          default: "0x48",
          list_values: ["0x48"],
          list_labels: ["0x48"],
        },
      ],
    },
  },
};
const emptyMap: ProbeMap = { probe_devices: [], probe_info: [] };

it("lists existing devices with module name", () => {
  const pm: ProbeMap = {
    probe_devices: [
      {
        device: "ADS1115",
        module: "ads1115_adafruit",
        module_filename: "ads1115_adafruit",
        ports: ["ADC0"],
        config: {},
      },
    ],
    probe_info: [],
  };
  render(<DevicesCard probeMap={pm} modules={modules} baseUrl="" onChange={rs.fn()} />);
  expect(screen.getByText("ADS1115")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "ADS1115 Adafruit" })).toBeInTheDocument();
});

it("adding a device runs the reducer and emits the new probe_map", async () => {
  const onChange = rs.fn();
  render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/add device module/i), {
    target: { value: "ads1115_adafruit" },
  });
  // default name pre-filled from friendly_name -> "ADS1115Adafruit"
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() =>
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        probe_devices: expect.arrayContaining([
          expect.objectContaining({ device: "ADS1115Adafruit" }),
        ]),
      }),
    ),
  );
});

it("surfaces a duplicate-name error without emitting", async () => {
  const pm: ProbeMap = {
    probe_devices: [
      {
        device: "ADS1115Adafruit",
        module: "ads1115_adafruit",
        module_filename: "ads1115_adafruit",
        ports: ["ADC0"],
        config: {},
      },
    ],
    probe_info: [],
  };
  const onChange = rs.fn();
  render(<DevicesCard probeMap={pm} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/add device module/i), {
    target: { value: "ads1115_adafruit" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/already exists/i));
  expect(onChange).not.toHaveBeenCalled();
  expect(validateBusKinds).not.toHaveBeenCalled();
});

it("blocks an add whose bus kind conflicts and shows the detail [inline validate]", async () => {
  (validateBusKinds as ReturnType<typeof rs.fn>).mockResolvedValueOnce({
    ok: false,
    detail: "'basic' I2C can't share a process with a USB-HID bus",
  });
  const onChange = rs.fn();
  render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/add device module/i), {
    target: { value: "ads1115_adafruit" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/USB-HID/i);
  expect(onChange).not.toHaveBeenCalled();
});

it("emits when the bus kind validates clean", async () => {
  const onChange = rs.fn();
  render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/add device module/i), {
    target: { value: "ads1115_adafruit" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(onChange).toHaveBeenCalled());
});

it("proceeds (fail-open) when validateBusKinds rejects [inline validate]", async () => {
  (validateBusKinds as ReturnType<typeof rs.fn>).mockRejectedValueOnce(new Error("network"));
  const onChange = rs.fn();
  render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText(/add device module/i), {
    target: { value: "ads1115_adafruit" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
  await waitFor(() => expect(onChange).toHaveBeenCalled());
});

// Deleting a device CASCADES (probeReducer.deleteDevice): every probe sitting
// on it goes too. These four cases pin that the user is told so first.
const pmWithAttachedProbe: ProbeMap = {
  probe_devices: [
    {
      device: "ADS1115",
      module: "ads1115_adafruit",
      module_filename: "ads1115_adafruit",
      ports: ["ADC0"],
      config: {},
    },
  ],
  probe_info: [
    {
      name: "Grill",
      label: "Grill",
      type: "Primary",
      enabled: true,
      device: "ADS1115",
      port: "ADC0",
      profile: {},
    },
  ],
};

describe("deleting a device", () => {
  it("asks first instead of deleting on the click", () => {
    const onChange = rs.fn();
    render(
      <DevicesCard
        probeMap={pmWithAttachedProbe}
        modules={modules}
        baseUrl=""
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(screen.getByText("Delete Probe Device?")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("names the cascade in the dialog", () => {
    render(
      <DevicesCard
        probeMap={pmWithAttachedProbe}
        modules={modules}
        baseUrl=""
        onChange={rs.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(
      screen.getByText("All probes associated with this device will also be deleted."),
    ).toBeInTheDocument();
  });

  it("cancelling closes the dialog and deletes nothing", () => {
    const onChange = rs.fn();
    render(
      <DevicesCard
        probeMap={pmWithAttachedProbe}
        modules={modules}
        baseUrl=""
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Delete Probe Device?")).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("ADS1115")).toBeInTheDocument();
  });

  it("confirming emits the cascade-updated map exactly once", () => {
    const onChange = rs.fn();
    render(
      <DevicesCard
        probeMap={pmWithAttachedProbe}
        modules={modules}
        baseUrl=""
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    // Both halves matter: the device row AND the probe that rode on it.
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ probe_devices: [], probe_info: [] }),
    );
    expect(screen.queryByText("Delete Probe Device?")).not.toBeInTheDocument();
  });
});

it("opening edit backfills manifest defaults absent from saved config", () => {
  const pm: ProbeMap = {
    probe_devices: [
      {
        device: "ADS1115",
        module: "ads1115_adafruit",
        module_filename: "ads1115_adafruit",
        ports: ["ADC0"],
        config: {},
      },
    ],
    probe_info: [],
  };
  render(<DevicesCard probeMap={pm} modules={modules} baseUrl="" onChange={rs.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  expect(screen.getByDisplayValue("0x48")).toBeInTheDocument();
});

it("cancelling the form clears it without emitting", () => {
  const pm: ProbeMap = {
    probe_devices: [
      {
        device: "ADS1115",
        module: "ads1115_adafruit",
        module_filename: "ads1115_adafruit",
        ports: ["ADC0"],
        config: {},
      },
    ],
    probe_info: [],
  };
  const onChange = rs.fn();
  render(<DevicesCard probeMap={pm} modules={modules} baseUrl="" onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /edit/i }));
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
});

describe("module select placeholder", () => {
  it("selecting the empty placeholder option does not open a form", () => {
    render(<DevicesCard probeMap={emptyMap} modules={modules} baseUrl="" onChange={rs.fn()} />);
    fireEvent.change(screen.getByLabelText(/add device module/i), { target: { value: "" } });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
