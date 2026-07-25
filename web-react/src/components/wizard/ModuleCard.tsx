import { scan } from "../../helpers/wizard/wizardApi";
import { moduleImageUrl } from "../../helpers/wizard/wizardAssets";
import type {
  SettingsDependency,
  WizardModuleData,
  WizardSection,
} from "../../helpers/wizard/wizardTypes";
import { ConfigOptionField } from "./ConfigOptionField";
import { I2cBusPicker } from "./fields/I2cBusPicker";
import { SelectField } from "./fields/SelectField";
import { UsbSerialPicker } from "./fields/UsbSerialPicker";

export type WizardConfigSource = "none" | "settings-by-module";

export interface ModuleCardProps {
  section: WizardSection;
  modules: Record<string, WizardModuleData>;
  selectedModule: string | null;
  depValues: Record<string, string | null>;
  configValues: Record<string, unknown>;
  configSource: WizardConfigSource;
  onSelectModule: (moduleName: string) => void;
  onDepChange: (key: string, value: string) => void;
  onConfigChange: (optionName: string, value: string) => void;
  baseUrl: string;
  disabled?: boolean;
}

export function ModuleCard({
  section,
  modules,
  selectedModule,
  depValues,
  configValues,
  configSource,
  onSelectModule,
  onDepChange,
  onConfigChange,
  baseUrl,
  disabled = false,
}: ModuleCardProps) {
  const selected = selectedModule ? modules[selectedModule] : undefined;

  function renderDep(key: string, dep: SettingsDependency) {
    if (dep.hidden) return null;
    const value = depValues[key] ?? "";
    const onChange = (v: string) => onDepChange(key, v);

    if (dep.type === "i2c_bus_num") {
      const kindKey = key.replace("_num", "_kind");
      return (
        <I2cBusPicker
          key={key}
          dep={dep}
          value={value}
          kindValue={depValues[kindKey] ?? ""}
          onChange={onChange}
          // The i2c discovery kind is the paired i2c_bus_kind field's value
          // (extended/mcp2221/ft232h) -- NOT the literal "i2c_bus_num", which
          // the backend /scan endpoint rejects as an unknown kind.
          onScan={() => scan(baseUrl, { kind: depValues[kindKey] ?? "" })}
        />
      );
    }

    if (dep.type === "usb_serial_device") {
      return (
        <UsbSerialPicker
          key={key}
          dep={dep}
          value={value}
          onChange={onChange}
          onScan={() => scan(baseUrl, { kind: "usb_serial" })}
        />
      );
    }

    const options = Object.entries(dep.options ?? {}).map(([optValue, label]) => ({
      value: optValue,
      label,
    }));
    return (
      <SelectField
        key={key}
        label={dep.friendly_name}
        value={value}
        options={options}
        onChange={onChange}
      />
    );
  }

  const showConfig = configSource === "settings-by-module" && (selected?.config?.length ?? 0) > 0;

  return (
    <div className="pf-module-card" data-section={section}>
      <label className="pf-field">
        <span className="pf-field-label">Module</span>
        <select
          className="pf-input"
          value={selectedModule ?? ""}
          disabled={disabled}
          onChange={(e) => onSelectModule(e.target.value)}
        >
          <option value="">— select —</option>
          {Object.entries(modules).map(([name, mod]) => (
            <option key={name} value={name}>
              {mod.friendly_name}
            </option>
          ))}
        </select>
      </label>

      {selected && (
        <div className="pf-module-details">
          {moduleImageUrl(baseUrl, selected.image) && (
            <img
              className="pf-module-image"
              src={moduleImageUrl(baseUrl, selected.image)}
              alt={selected.friendly_name}
            />
          )}
          <h3 className="pf-module-name">{selected.friendly_name}</h3>
          {selected.description && <p className="pf-module-description">{selected.description}</p>}
          {selected.notes && <p className="pf-module-notes">{selected.notes}</p>}

          {Object.keys(selected.settings_dependencies).length > 0 && (
            <div className="pf-module-deps">
              {Object.entries(selected.settings_dependencies).map(([key, dep]) =>
                renderDep(key, dep),
              )}
            </div>
          )}

          {showConfig && (
            <div className="pf-module-config">
              {(selected.config ?? []).map((option) => (
                <ConfigOptionField
                  key={option.option_name}
                  option={option}
                  value={configValues[option.option_name]}
                  onChange={(v) => onConfigChange(option.option_name, v)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
