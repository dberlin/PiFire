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

describe("UsbSerialPicker", () => {
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
