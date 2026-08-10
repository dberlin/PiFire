import type { I2cBusValue } from "../contracts/wizard.gen";

/** Helpers for the generated I2C bus union. Switching kind replaces the
 * object rather than leaving a selector behind that the new kind cannot use. */

export type KernelBy = "bus_num" | "adapter" | "serial";
export type BusKind = NonNullable<I2cBusValue["kind"]>;

export const BUS_KIND_LABELS: Record<BusKind, string> = {
  basic: "Basic (integrated I2C bus)",
  kernel: "Kernel (/dev/i2c-N adapter)",
  ft232h: "FT232H (USB)",
  mcp2221: "MCP2221 (USB)",
};

export const KERNEL_BY_LABELS: Record<KernelBy, string> = {
  bus_num: "Bus number",
  adapter: "Adapter name",
  serial: "USB serial",
};

/** The empty value for a kind, used when the operator switches kinds. */
export function emptyBus(kind: BusKind, by: KernelBy = "adapter"): I2cBusValue {
  if (kind === "basic") return { kind: "basic" };
  if (kind === "ft232h") return { kind: "ft232h", url: "" };
  if (kind === "mcp2221") return { kind: "mcp2221", serial: "" };
  if (by === "bus_num") return { kind: "kernel", bus_num: null };
  if (by === "serial") return { kind: "kernel", serial: "" };
  return { kind: "kernel", adapter: "" };
}

export function kernelBy(bus: I2cBusValue): KernelBy {
  if (bus.kind !== "kernel") return "adapter";
  if ("bus_num" in bus) return "bus_num";
  if ("serial" in bus) return "serial";
  return "adapter";
}

/** The per-field format rules. The XOR between kernel selectors is not checked
 *  here -- the type makes it unrepresentable. Python keeps the authoritative
 *  copy of these rules for configs that arrive by import or by hand; see
 *  tests/web/test_i2c_bus_rule_parity.py. */
export function i2cBusError(bus: I2cBusValue): string | null {
  if (bus.kind === "kernel") {
    if ("bus_num" in bus) {
      return bus.bus_num !== null && Number.isInteger(bus.bus_num) && bus.bus_num >= 0
        ? null
        : "Enter the bus number, the N in /dev/i2c-N.";
    }
    if ("adapter" in bus) {
      return bus.adapter.trim() ? null : "Enter the adapter name, e.g. CP2112.";
    }
    return bus.serial.trim() ? null : "Enter the adapter's USB serial.";
  }
  if (bus.kind === "ft232h") {
    if (!bus.url.trim()) return null;
    return bus.url.startsWith("ftdi://")
      ? null
      : "An FT232H URL starts with ftdi:// — or leave it blank for the first one found.";
  }
  return null;
}
