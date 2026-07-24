import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { scanThermoworks } from "../../../helpers/wizard/wizardApi";
import { ThermoworksPicker } from "./ThermoworksPicker";

rs.mock("../../../helpers/wizard/wizardApi", () => ({
  scanThermoworks: rs.fn(),
}));

afterEach(cleanup);

describe("ThermoworksPicker", () => {
  it("renders the current value as read-only", () => {
    render(
      <ThermoworksPicker value="SN123" email="a@b.c" password="x" baseUrl="" onPick={rs.fn()} />,
    );
    expect(screen.getByRole("textbox")).toHaveValue("SN123");
    expect(screen.getByRole("textbox")).toHaveAttribute("readonly");
  });

  it("tests the connection, renders result rows, and picking a row calls onPick", async () => {
    (scanThermoworks as ReturnType<typeof rs.fn>).mockResolvedValue({
      rows: [{ label: "Signals", type: "signals", serial: "SN123", num_channels: 4 }],
      error: null,
    });
    const onPick = rs.fn();
    render(
      <ThermoworksPicker value="" email="a@b.c" password="secret" baseUrl="" onPick={onPick} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));

    expect(
      await screen.findByRole("button", { name: /Signals \(SN123\) — 4 probes/ }),
    ).toBeInTheDocument();
    expect(scanThermoworks).toHaveBeenCalledWith("", "a@b.c", "secret");

    fireEvent.click(screen.getByRole("button", { name: /Signals \(SN123\) — 4 probes/ }));
    expect(onPick).toHaveBeenCalledWith({
      label: "Signals",
      type: "signals",
      serial: "SN123",
      num_channels: 4,
    });
  });

  it("renders the connection error", async () => {
    (scanThermoworks as ReturnType<typeof rs.fn>).mockResolvedValue({
      rows: [],
      error: "Invalid credentials.",
    });
    render(<ThermoworksPicker value="" email="a@b.c" password="x" baseUrl="" onPick={rs.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid credentials.");
  });
});
