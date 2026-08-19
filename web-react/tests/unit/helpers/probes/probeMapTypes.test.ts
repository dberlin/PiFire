import type { ProbeModuleCatalog } from "@pifire/core/contracts/wizard";
import { describe, expect, it } from "@rstest/core";

// Pinned against blueprints/api/routes.py::_api_get_probe_modules, which
// returns api_response(data={"modules": ..., "requires_install": ...}) --
// the {data,result,message} envelope from common/app.py:422-431.
const WIRE = {
  data: {
    modules: {
      ds18b20: {
        friendly_name: "DS18B20",
        filename: "ds18b20",
        image: "ds18b20.png",
        device_specific: { ports: ["DS0"], type: "1wire", config: [] },
      },
    },
    requires_install: { ds18b20: true },
  },
  result: "OK",
  message: null,
};

describe("probe module catalog seam", () => {
  it("carries the two maps the tab needs, keyed alike", () => {
    const catalog: ProbeModuleCatalog = WIRE.data as ProbeModuleCatalog;
    expect(Object.keys(catalog.modules)).toEqual(Object.keys(catalog.requires_install));
    expect(catalog.requires_install.ds18b20).toBe(true);
  });

  it("exposes exactly what DevicesCard reads off a module", () => {
    const mod = (WIRE.data as ProbeModuleCatalog).modules.ds18b20;
    // DevicesCard.tsx:120-130 and DeviceForm.tsx:23-34.
    expect(mod.friendly_name).toBe("DS18B20");
    expect(mod.image).toBe("ds18b20.png");
    expect(mod.device_specific.ports).toEqual(["DS0"]);
    expect(Array.isArray(mod.device_specific.config)).toBe(true);
  });
});
