import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ModuleCard } from "../../../../src/components/wizard/ModuleCard";
import type { I2cBusValue, WizardModuleData } from "../../../../src/helpers/contracts/wizard.gen";
import { scan } from "../../../../src/helpers/wizard/wizardApi";

rs.mock("../../../../src/helpers/wizard/wizardApi", () => ({
  scan: rs.fn().mockResolvedValue({ groups: [], error: null }),
}));

afterEach(cleanup);

const basicModule: WizardModuleData = {
  friendly_name: "Basic PWM Fan",
  description: "A basic PWM controlled fan.",
  notes: "Requires a spare GPIO pin.",
  settings_dependencies: {
    gpio_pin: {
      friendly_name: "GPIO Pin",
      options: { "17": "GPIO 17", "27": "GPIO 27" },
      settings: ["gpio_pin"],
    },
  },
};

const moduleWithHiddenDep: WizardModuleData = {
  friendly_name: "Hidden Dep Module",
  settings_dependencies: {
    secret: {
      friendly_name: "Secret",
      hidden: true,
      settings: ["secret"],
    },
  },
};

const moduleWithI2c: WizardModuleData = {
  friendly_name: "I2C Module",
  settings_dependencies: {
    i2c_bus: {
      friendly_name: "I2C Bus",
      type: "i2c_bus",
      default: { kind: "basic" },
      settings: ["i2c_bus"],
    },
  },
};

const moduleWithUsbSerial: WizardModuleData = {
  friendly_name: "USB Serial Module",
  settings_dependencies: {
    usb_device: {
      friendly_name: "USB Device",
      type: "usb_serial_device",
      options: { "/dev/ttyUSB0": "ttyUSB0" },
      settings: ["usb_device"],
    },
  },
};

const moduleWithMcp2221Serial: WizardModuleData = {
  friendly_name: "MCP2221 Relay Module",
  settings_dependencies: {
    mcp2221_serial: {
      friendly_name: "Relay MCP2221A Device",
      type: "mcp2221_serial",
      settings: ["platform", "mcp2221", "serial"],
    },
  },
};

// wizard_manifest.json stores a BARE filename here; the card has to resolve it
// against PiFire's own origin or every board photo 404s.
const moduleWithImage: WizardModuleData = {
  friendly_name: "PCB 4.x.x",
  image: "pcb_4.x.x.png",
  settings_dependencies: {},
};

const moduleWithConfig: WizardModuleData = {
  friendly_name: "Configurable Module",
  settings_dependencies: {},
  config: [
    {
      option_name: "units",
      option_friendly_name: "Units",
      option_type: "list",
      list_values: ["F", "C"],
      list_labels: ["Fahrenheit", "Celsius"],
      default: "F",
    },
  ],
};

const modules: Record<string, WizardModuleData> = {
  basic: basicModule,
  hidden_dep: moduleWithHiddenDep,
  i2c: moduleWithI2c,
  usb_serial: moduleWithUsbSerial,
  imaged: moduleWithImage,
  configurable: moduleWithConfig,
  mcp2221_serial: moduleWithMcp2221Serial,
};

function baseProps() {
  return {
    section: "distance" as const,
    modules,
    selectedModule: null as string | null,
    depValues: {} as Record<string, string | I2cBusValue | null>,
    configValues: {} as Record<string, unknown>,
    configSource: "none" as const,
    onSelectModule: rs.fn(),
    onDepChange: rs.fn(),
    onConfigChange: rs.fn(),
    baseUrl: "",
  };
}

describe("ModuleCard", () => {
  it("renders the placeholder and no crash when selectedModule is null", () => {
    render(<ModuleCard {...baseProps()} />);
    expect(screen.getByRole("combobox", { name: "Module" })).toHaveValue("");
    expect(screen.getByText("— select —")).toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("renders module options from modules and calls onSelectModule on change with no fetch", () => {
    const onSelectModule = rs.fn();
    render(<ModuleCard {...baseProps()} onSelectModule={onSelectModule} />);

    const select = screen.getByRole("combobox", { name: "Module" });
    expect(screen.getByText("Basic PWM Fan")).toBeInTheDocument();
    expect(screen.getByText("I2C Module")).toBeInTheDocument();

    fireEvent.change(select, { target: { value: "basic" } });

    expect(onSelectModule).toHaveBeenCalledWith("basic");
    expect(onSelectModule).toHaveBeenCalledTimes(1);
  });

  it("renders the selected module's identity details", () => {
    render(<ModuleCard {...baseProps()} selectedModule="basic" />);
    expect(screen.getByRole("heading", { name: "Basic PWM Fan" })).toBeInTheDocument();
    expect(screen.getByText("A basic PWM controlled fan.")).toBeInTheDocument();
    expect(screen.getByText("Requires a spare GPIO pin.")).toBeInTheDocument();
  });

  it("renders a SelectField for a non-hidden settings dependency and wires onDepChange", () => {
    const onDepChange = rs.fn();
    render(
      <ModuleCard
        {...baseProps()}
        selectedModule="basic"
        depValues={{ gpio_pin: "17" }}
        onDepChange={onDepChange}
      />,
    );
    const field = screen.getByRole("combobox", { name: "GPIO Pin" });
    expect(field).toHaveValue("17");

    fireEvent.change(field, { target: { value: "27" } });
    expect(onDepChange).toHaveBeenCalledWith("gpio_pin", "27");
  });

  it("renders nothing for a hidden settings dependency", () => {
    render(<ModuleCard {...baseProps()} selectedModule="hidden_dep" />);
    expect(screen.queryByText("Secret")).not.toBeInTheDocument();
  });

  it("renders the I2cBusField (with Discover button) for an i2c_bus dependency", async () => {
    const { scan } = await import("../../../../src/helpers/wizard/wizardApi");
    // The scan kind comes from the bus value's own `kind`, not from a paired
    // field -- an I2cBusValue carries its kind, so there is nothing else to
    // forward.
    const bus: I2cBusValue = { kind: "kernel", adapter: "CP2112" };
    render(
      <ModuleCard
        {...baseProps()}
        baseUrl="http://localhost"
        selectedModule="i2c"
        depValues={{ i2c_bus: bus }}
      />,
    );
    expect(screen.getByText("I2C Bus")).toBeInTheDocument();
    const discoverButton = screen.getByRole("button", { name: /discover/i });
    expect(discoverButton).toBeInTheDocument();

    fireEvent.click(discoverButton);
    expect(scan).toHaveBeenCalledWith("http://localhost", { kind: "kernel" });
  });

  it("renders the UsbSerialPicker (with Discover button) for a usb_serial_device dependency", async () => {
    const { scan } = await import("../../../../src/helpers/wizard/wizardApi");
    render(<ModuleCard {...baseProps()} selectedModule="usb_serial" />);
    expect(screen.getByText("USB Device")).toBeInTheDocument();
    const discoverButton = screen.getByRole("button", { name: /discover/i });
    expect(discoverButton).toBeInTheDocument();

    fireEvent.click(discoverButton);
    expect(scan).toHaveBeenCalledWith("", {
      kind: "usb_serial",
      vid: undefined,
      pid: undefined,
    });
  });

  it("narrows the Discover scan to the dependency's USB IDs when the manifest names them", async () => {
    // The manifest declared vid/pid for the Numato relay and the call site
    // dropped them, so Discover listed every serial device on the machine --
    // including the one that had been mistaken for the relay. The IDs are
    // forwarded verbatim; common/usb_serial.py coerces the hex string.
    const { scan } = await import("../../../../src/helpers/wizard/wizardApi");
    // A local module fixture, not a mutation of the shared `modules` object --
    // baseProps() hands out the same one to every test in this file.
    const identified: Record<string, WizardModuleData> = {
      usb_serial: {
        friendly_name: "USB Serial Module",
        settings_dependencies: {
          usb_device: {
            friendly_name: "USB Device",
            type: "usb_serial_device",
            options: { "/dev/ttyUSB0": "ttyUSB0" },
            settings: ["usb_device"],
            vid: "0x2a19",
            pid: "0x0c0c",
          },
        },
      },
    };

    render(<ModuleCard {...baseProps()} modules={identified} selectedModule="usb_serial" />);
    fireEvent.click(screen.getByRole("button", { name: /discover/i }));

    expect(scan).toHaveBeenCalledWith("", {
      kind: "usb_serial",
      vid: "0x2a19",
      pid: "0x0c0c",
    });
  });

  it("discovers and selects a specific MCP2221 serial for the relay adapter", async () => {
    rs.mocked(scan).mockResolvedValueOnce({
      groups: [
        {
          title: "MCP2221 Devices",
          items: [{ value: "RELAY-B", label: "MCP2221 RELAY-B" }],
        },
      ],
      error: null,
    });
    const onDepChange = rs.fn();

    render(
      <ModuleCard
        {...baseProps()}
        baseUrl="http://localhost"
        selectedModule="mcp2221_serial"
        depValues={{ mcp2221_serial: "" }}
        onDepChange={onDepChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /discover/i }));
    expect(scan).toHaveBeenCalledWith("http://localhost", { kind: "mcp2221" });

    fireEvent.click(await screen.findByRole("button", { name: "MCP2221 RELAY-B" }));
    expect(onDepChange).toHaveBeenCalledWith("mcp2221_serial", "RELAY-B");
  });

  it("resolves the manifest's bare image filename against PiFire's static path", () => {
    render(<ModuleCard {...baseProps()} selectedModule="imaged" />);
    expect(screen.getByRole("img", { name: "PCB 4.x.x" })).toHaveAttribute(
      "src",
      "/static/img/wizard/pcb_4.x.x.png",
    );
  });

  it("prefixes the image with a remote PiFire origin when one is configured", () => {
    render(
      <ModuleCard {...baseProps()} baseUrl="http://pifire.local:5000" selectedModule="imaged" />,
    );
    expect(screen.getByRole("img", { name: "PCB 4.x.x" })).toHaveAttribute(
      "src",
      "http://pifire.local:5000/static/img/wizard/pcb_4.x.x.png",
    );
  });

  it("renders no <img> for a module the manifest gives no image", () => {
    render(<ModuleCard {...baseProps()} selectedModule="basic" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("renders no config table when configSource is none, even if the module has config", () => {
    render(<ModuleCard {...baseProps()} selectedModule="configurable" configSource="none" />);
    expect(screen.queryByText("Units")).not.toBeInTheDocument();
  });

  it("renders ConfigOptionFields when configSource is settings-by-module and config values are present", () => {
    const onConfigChange = rs.fn();
    render(
      <ModuleCard
        {...baseProps()}
        selectedModule="configurable"
        configSource="settings-by-module"
        configValues={{ units: "C" }}
        onConfigChange={onConfigChange}
      />,
    );
    const field = screen.getByRole("combobox", { name: "Units" });
    expect(field).toHaveValue("C");

    fireEvent.change(field, { target: { value: "F" } });
    expect(onConfigChange).toHaveBeenCalledWith("units", "F");
  });

  it("renders the config table using option defaults without throwing when configValues is {}", () => {
    expect(() =>
      render(
        <ModuleCard
          {...baseProps()}
          selectedModule="configurable"
          configSource="settings-by-module"
          configValues={{}}
        />,
      ),
    ).not.toThrow();
    expect(screen.getByText("Units")).toBeInTheDocument();
  });
});
