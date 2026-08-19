import type { I2CBusValue } from "@pifire/core/contracts/wizard";

/** Helpers for the generated I2C bus union. Switching kind replaces the
 * object rather than leaving a selector behind that the new kind cannot use. */

export type KernelBy = "bus_num" | "adapter" | "serial";
export type BusKind = NonNullable<I2CBusValue["kind"]>;

export const BUS_KINDS: BusKind[] = ["basic", "kernel", "ft232h", "mcp2221"];
export const KERNEL_BY_OPTIONS: KernelBy[] = ["bus_num", "adapter", "serial"];
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

export function isI2CBusValue(value: unknown): value is I2CBusValue & { kind: BusKind } {
  if (typeof value !== "object" || value === null || !("kind" in value)) return false;
  if (value.kind === "basic") return true;
  if (value.kind === "ft232h") return "url" in value && typeof value.url === "string";
  if (value.kind === "mcp2221") return "serial" in value && typeof value.serial === "string";
  if (value.kind !== "kernel") return false;
  return (
    ("bus_num" in value && (value.bus_num === null || typeof value.bus_num === "number")) ||
    ("adapter" in value && typeof value.adapter === "string") ||
    ("serial" in value && typeof value.serial === "string")
  );
}

/** The empty value for a kind, used when the operator switches kinds. */
export function emptyBus(kind: BusKind, by: KernelBy = "adapter"): I2CBusValue & { kind: BusKind } {
  if (kind === "basic") return { kind: "basic" };
  if (kind === "ft232h") return { kind: "ft232h", url: "" };
  if (kind === "mcp2221") return { kind: "mcp2221", serial: "" };
  if (by === "bus_num") return { kind: "kernel", bus_num: null };
  if (by === "serial") return { kind: "kernel", serial: "" };
  return { kind: "kernel", adapter: "" };
}

export function kernelBy(bus: I2CBusValue): KernelBy {
  if (bus.kind !== "kernel") return "adapter";
  if ("bus_num" in bus) return "bus_num";
  if ("serial" in bus) return "serial";
  return "adapter";
}

/** The per-field format rules. The XOR between kernel selectors is not checked
 *  here -- the type makes it unrepresentable. Python keeps the authoritative
 *  copy of these rules for configs that arrive by import or by hand; see
 *  tests/web/test_i2c_bus_rule_parity.py. */
export function i2cBusError(bus: I2CBusValue): string | null {
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
    const url = bus.url ?? "";
    if (!url.trim()) return null;
    return url.startsWith("ftdi://")
      ? null
      : "An FT232H URL starts with ftdi:// — or leave it blank for the first one found.";
  }
  return null;
}
