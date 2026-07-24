import { useState } from "react";
import {
  addDevice,
  alnum,
  availableProbes,
  deleteDevice,
  editDevice,
} from "../../../helpers/wizard/probeReducer";
import type { ProbeMap, ProbeModuleData } from "../../../helpers/wizard/probeTypes";
import { DeviceForm } from "./DeviceForm";

export interface DevicesCardProps {
  probeMap: ProbeMap;
  modules: Record<string, ProbeModuleData>;
  baseUrl: string;
  onChange: (next: ProbeMap) => void;
}

interface FormState {
  mode: "add" | "edit";
  module: string;
  originalName: string; // edit only
  name: string;
  values: Record<string, unknown>;
}

function defaultsFor(mod: ProbeModuleData): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of mod.device_specific.config) {
    out[f.label] = f.type === "probes_list" ? [] : (f.default ?? "");
  }
  return out;
}

export function DevicesCard({ probeMap, modules, baseUrl, onChange }: DevicesCardProps) {
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const probes = availableProbes(probeMap);

  function openAdd(module: string) {
    const mod = modules[module];
    if (!mod) return;
    setError(null);
    setForm({
      mode: "add",
      module,
      originalName: "",
      name: alnum(mod.friendly_name),
      values: defaultsFor(mod),
    });
  }

  function openEdit(device: ProbeMap["probe_devices"][number]) {
    const mod = modules[device.module];
    if (!mod) return;
    // §2 backfill: manifest fields absent from saved config get their default.
    const values = { ...defaultsFor(mod), ...device.config };
    setError(null);
    setForm({
      mode: "edit",
      module: device.module,
      originalName: device.device,
      name: device.device,
      values,
    });
  }

  function submit() {
    if (!form) return;
    const mod = modules[form.module];
    const result =
      form.mode === "add"
        ? addDevice(probeMap, {
            name: form.name,
            module: form.module,
            moduleData: mod,
            config: form.values,
          })
        : editDevice(probeMap, {
            originalName: form.originalName,
            newName: form.name,
            config: form.values,
          });
    if (result.ok) {
      onChange(result.probeMap);
      setForm(null);
      setError(null);
    } else {
      setError(result.error);
    }
  }

  return (
    <section className="pf-probes-card" aria-label="Probe devices">
      <h3>Devices</h3>
      <table className="pf-probes-table">
        <tbody>
          {probeMap.probe_devices.map((d) => (
            <tr key={d.device}>
              <td>
                {modules[d.module]?.image && (
                  <img src={modules[d.module].image} alt="" width={48} height={48} />
                )}
              </td>
              <td>{d.device}</td>
              <td>{modules[d.module]?.friendly_name ?? d.module}</td>
              <td>
                <button type="button" onClick={() => openEdit(d)}>
                  Edit
                </button>
                <button type="button" onClick={() => onChange(deleteDevice(probeMap, d.device))}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {!form && (
        <label className="pf-field">
          <span className="pf-field-label">Add Device — Module</span>
          <select
            className="pf-input"
            defaultValue=""
            onChange={(e) => e.target.value && openAdd(e.target.value)}
            aria-label="Add device module"
          >
            <option value="">— add device —</option>
            {Object.entries(modules).map(([key, mod]) => (
              <option key={key} value={key}>
                {mod.friendly_name}
              </option>
            ))}
          </select>
        </label>
      )}

      {form && (
        <DeviceForm
          mode={form.mode}
          moduleData={modules[form.module]}
          values={form.values}
          nameValue={form.name}
          availableProbes={probes}
          baseUrl={baseUrl}
          error={error}
          onNameChange={(name) => setForm({ ...form, name })}
          onFieldChange={(label, value) =>
            setForm({ ...form, values: { ...form.values, [label]: value } })
          }
          onSubmit={submit}
          onCancel={() => {
            setForm(null);
            setError(null);
          }}
        />
      )}
    </section>
  );
}
