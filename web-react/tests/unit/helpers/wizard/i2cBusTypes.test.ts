import { describe, expect, it } from "@rstest/core";
import type { I2CBusValue } from "../../../../src/helpers/contracts/wizard.gen";
import { i2cBusError } from "../../../../src/helpers/wizard/i2cBusTypes";

describe("i2cBusError", () => {
  it("accepts a basic bus, which has nothing to fill in", () => {
    expect(i2cBusError({ kind: "basic" })).toBe(null);
  });

  it("requires a kernel bus number", () => {
    expect(i2cBusError({ kind: "kernel", bus_num: null })).toMatch(/bus number/i);
  });

  it("survives a JSON round trip, the way a saved draft does", () => {
    const unfilled: I2CBusValue = { kind: "kernel", bus_num: null };
    expect(JSON.parse(JSON.stringify(unfilled))).toEqual(unfilled);
  });

  it("accepts a kernel bus number", () => {
    expect(i2cBusError({ kind: "kernel", bus_num: 3 })).toBe(null);
  });

  it("requires a kernel adapter name", () => {
    expect(i2cBusError({ kind: "kernel", adapter: "  " })).toMatch(/adapter/i);
    expect(i2cBusError({ kind: "kernel", adapter: "CP2112" })).toBe(null);
  });

  it("requires a kernel serial", () => {
    expect(i2cBusError({ kind: "kernel", serial: "" })).toMatch(/serial/i);
    expect(i2cBusError({ kind: "kernel", serial: "AB12" })).toBe(null);
  });

  it("lets an ft232h url be blank, meaning the first device found", () => {
    expect(i2cBusError({ kind: "ft232h", url: "" })).toBe(null);
    expect(i2cBusError({ kind: "ft232h", url: "ftdi://ftdi:232h:FT9/1" })).toBe(null);
  });

  it("rejects an ft232h url that is not a pyftdi url", () => {
    expect(i2cBusError({ kind: "ft232h", url: "CP2112" })).toMatch(/ftdi:\/\//);
  });

  it("lets an mcp2221 serial be blank, meaning the first device found", () => {
    expect(i2cBusError({ kind: "mcp2221", serial: "" })).toBe(null);
  });
});
