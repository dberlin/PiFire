import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { scan } from "../../../helpers/wizard/wizardApi";
import type { SettingsDependency } from "../../../helpers/wizard/wizardTypes";
import { UsbSerialPicker } from "./UsbSerialPicker";

rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scan: rs.fn(),
}));

afterEach(cleanup);

const dep: SettingsDependency = {
  friendly_name: "USB Serial Device",
  type: "usb_serial_device",
  options: { "/dev/ttyUSB0": "/dev/ttyUSB0" },
  settings: ["usb_serial_device"],
};

// The real manifest shape of distance.sen0628.sen0628_device: a `default` plus
// a short, non-exhaustive list of /dev/tty* paths. A board can sit on any of
// them, so the list is a suggestion, never the set of permitted values.
const manifestDep: SettingsDependency = {
  friendly_name: "Serial Device (USB)",
  description: "The USB serial device path the sensor enumerates as.",
  type: "usb_serial_device",
  default: "/dev/ttyACM0",
  options: {
    "/dev/ttyACM0": "/dev/ttyACM0",
    "/dev/ttyACM1": "/dev/ttyACM1",
    "/dev/ttyUSB0": "/dev/ttyUSB0",
    "/dev/ttyUSB1": "/dev/ttyUSB1",
  },
  settings: ["platform", "devices", "distance", "usb_serial_device"],
};

describe("UsbSerialPicker", () => {
  it("renders a text input, not a select, so a manifest dep with no options is still fillable", () => {
    const { container } = render(
      <UsbSerialPicker
        dep={{ ...manifestDep, options: undefined }}
        value="/dev/ttyACM0"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "usb_serial" })}
      />,
    );
    const input = screen.getByLabelText("Serial Device (USB)");
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveValue("/dev/ttyACM0");
    expect(container.querySelector("select")).toBeNull();
  });

  it("accepts a path the manifest options do not list", () => {
    const onChange = rs.fn();
    render(
      <UsbSerialPicker
        dep={manifestDep}
        value="/dev/ttyACM0"
        onChange={onChange}
        onScan={() => scan("", { kind: "usb_serial" })}
      />,
    );
    fireEvent.change(screen.getByLabelText("Serial Device (USB)"), {
      target: { value: "/dev/ttyACM2" },
    });
    expect(onChange).toHaveBeenCalledWith("/dev/ttyACM2");
  });

  it("renders the dep description", () => {
    render(
      <UsbSerialPicker
        dep={manifestDep}
        value=""
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "usb_serial" })}
      />,
    );
    expect(
      screen.getByText("The USB serial device path the sensor enumerates as."),
    ).toBeInTheDocument();
  });

  it("offers manifest options as non-binding datalist suggestions when present", () => {
    const { container } = render(
      <UsbSerialPicker
        dep={manifestDep}
        value="/dev/ttyACM0"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "usb_serial" })}
      />,
    );
    const input = screen.getByLabelText("Serial Device (USB)");
    const listId = input.getAttribute("list");
    expect(listId).toBeTruthy();
    const datalist = container.querySelector(`datalist[id="${listId}"]`);
    expect(datalist).not.toBeNull();
    expect(Array.from(datalist!.querySelectorAll("option"), (o) => o.value)).toEqual([
      "/dev/ttyACM0",
      "/dev/ttyACM1",
      "/dev/ttyUSB0",
      "/dev/ttyUSB1",
    ]);
  });

  it("renders the field", () => {
    render(
      <UsbSerialPicker
        dep={dep}
        value="/dev/ttyUSB0"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "usb_serial_device" })}
      />,
    );
    expect(screen.getByText("USB Serial Device")).toBeInTheDocument();
  });

  it("renders nothing when dep is hidden", () => {
    const { container } = render(
      <UsbSerialPicker
        dep={{ ...dep, hidden: true }}
        value="/dev/ttyUSB0"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "usb_serial_device" })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("calls onScan on Discover and renders the results panel", async () => {
    (scan as ReturnType<typeof rs.fn>).mockResolvedValue({
      groups: [
        { title: "By Serial", items: [{ value: "/dev/ttyUSB1", label: "/dev/ttyUSB1 (FTDI)" }] },
      ],
      error: null,
    });
    const onChange = rs.fn();
    render(
      <UsbSerialPicker
        dep={dep}
        value="/dev/ttyUSB0"
        onChange={onChange}
        onScan={() => scan("", { kind: "usb_serial_device" })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /discover/i }));

    expect(await screen.findByRole("button", { name: "/dev/ttyUSB1 (FTDI)" })).toBeInTheDocument();
    expect(scan).toHaveBeenCalledWith("", { kind: "usb_serial_device" });

    fireEvent.click(screen.getByRole("button", { name: "/dev/ttyUSB1 (FTDI)" }));
    expect(onChange).toHaveBeenCalledWith("/dev/ttyUSB1");
  });

  it("renders the discovery error instead of a panel", async () => {
    (scan as ReturnType<typeof rs.fn>).mockResolvedValue({
      groups: [],
      error: "No USB serial devices found.",
    });
    render(
      <UsbSerialPicker
        dep={dep}
        value="/dev/ttyUSB0"
        onChange={rs.fn()}
        onScan={() => scan("", { kind: "usb_serial_device" })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /discover/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No USB serial devices found.");
  });
});
