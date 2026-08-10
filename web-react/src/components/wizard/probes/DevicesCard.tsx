import { useState } from "react";
import {
  addDevice,
  alnum,
  availableProbes,
  deleteDevice,
  editDevice,
} from "../../../helpers/wizard/probeReducer";
import type { Config, ProbeMap, ProbeModuleData } from "../../../helpers/contracts/wizard.gen";
import { validateBusKinds } from "../../../helpers/wizard/wizardApi";
import { moduleImageUrl } from "../../../helpers/wizard/wizardAssets";
import { ConfirmAction } from "../../dashboard/ConfirmAction";
import { DeviceForm } from "./DeviceForm";
import "./probes.css";

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
  values: Config;
}

function defaultsFor(mod: ProbeModuleData): Config {
  const output: Config = {};
  for (const field of mod.device_specific.config) {
    output[field.label] = field.type === "probes_list" ? [] : (field.default ?? "");
  }
  return output;
}

export function DevicesCard({ probeMap, modules, baseUrl, onChange }: DevicesCardProps) {
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
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

  async function submit() {
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
    if (!result.ok) {
      setError(result.error);
      return;
    }
    // In-progress bus-kind coexistence check (§7). The full cross-subsystem
    // check still runs at /finish; this is inline pre-Finish feedback.
    let verdict: { ok: boolean; detail?: string };
    try {
      verdict = await validateBusKinds(baseUrl, result.probeMap.probe_devices);
    } catch (err) {
      // Advisory check; the authoritative bus-kind validation runs at /finish.
      // Don't strand the user on a transient validate failure -- proceed.
      console.warn("Wizard: bus-kind validation unavailable, proceeding", err);
      verdict = { ok: true };
    }
    if (!verdict.ok) {
      setError(verdict.detail ?? "This device's bus configuration conflicts with another device.");
      return;
    }
    onChange(result.probeMap);
    setForm(null);
    setError(null);
  }

  return (
    <section className="pf-probes-card" aria-label="Probe devices">
      <h3>Devices</h3>
      <table className="pf-probes-table">
        <tbody>
          {probeMap.probe_devices.map((d) => (
            <tr key={d.device}>
              <td>
                {moduleImageUrl(baseUrl, modules[d.module]?.image) && (
                  <img
                    src={moduleImageUrl(baseUrl, modules[d.module]?.image)}
                    alt=""
                    width={48}
                    height={48}
                  />
                )}
              </td>
              <td>{d.device}</td>
              <td>{modules[d.module]?.friendly_name ?? d.module}</td>
              <td>
                <button type="button" onClick={() => openEdit(d)}>
                  Edit
                </button>
                <button type="button" onClick={() => setPendingDelete(d.device)}>
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
          onSubmit={() => void submit()}
          onCancel={() => {
            setForm(null);
            setError(null);
          }}
        />
      )}

      {/* Deleting a device CASCADES: probeReducer.deleteDevice (probeReducer.ts:113-129)
          drops every probe_info row whose `device` matches and scrubs those labels out
          of any virtual device's probes_list. Legacy warned about exactly this before
          acting -- _macro_probes_config.html:70-89 ("delProbeDeviceModal"). */}
      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete Probe Device?"
        message="All probes associated with this device will also be deleted."
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete !== null) onChange(deleteDevice(probeMap, pendingDelete));
          setPendingDelete(null);
        }}
      />
    </section>
  );
}
