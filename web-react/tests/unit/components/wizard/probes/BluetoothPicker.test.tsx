import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BluetoothPicker } from "../../../../../src/components/wizard/probes/BluetoothPicker";
import { scanBluetooth } from "../../../../../src/helpers/wizard/wizardApi";

rs.mock("../../../../../src/helpers/wizard/wizardApi", () => ({
  scanBluetooth: rs.fn(),
}));

afterEach(cleanup);

describe("BluetoothPicker", () => {
  it("renders the field with the current value", () => {
    render(<BluetoothPicker label="BT Address" value="AA:BB" baseUrl="" onChange={rs.fn()} />);
    expect(screen.getByText("BT Address")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("AA:BB");
  });

  it("types directly into the field and calls onChange", () => {
    const onChange = rs.fn();
    render(<BluetoothPicker label="BT Address" value="" baseUrl="" onChange={onChange} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "CC:DD" } });
    expect(onChange).toHaveBeenCalledWith("CC:DD");
  });

  it("scans, renders result rows, and picking a row calls onChange with hw_id", async () => {
    (scanBluetooth as ReturnType<typeof rs.fn>).mockResolvedValue({
      rows: [{ name: "Fireboard", hw_id: "AA:BB:CC", info: "RSSI -60" }],
      error: null,
    });
    const onChange = rs.fn();
    render(<BluetoothPicker label="BT Address" value="" baseUrl="" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /scan/i }));

    expect(
      await screen.findByRole("button", { name: /Fireboard \[AA:BB:CC\] RSSI -60/ }),
    ).toBeInTheDocument();
    expect(scanBluetooth).toHaveBeenCalledWith("");

    fireEvent.click(screen.getByRole("button", { name: /Fireboard \[AA:BB:CC\] RSSI -60/ }));
    expect(onChange).toHaveBeenCalledWith("AA:BB:CC");
  });

  it("renders the scan error", async () => {
    (scanBluetooth as ReturnType<typeof rs.fn>).mockResolvedValue({
      rows: [],
      error: "Bluetooth adapter not found.",
    });
    render(<BluetoothPicker label="BT Address" value="" baseUrl="" onChange={rs.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /scan/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bluetooth adapter not found.");
  });
});
