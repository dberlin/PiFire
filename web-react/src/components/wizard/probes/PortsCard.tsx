import type { ProbeMap, ProbeProfile } from "@pifire/core/contracts/wizard";
import { useState } from "react";
import {
  addProbe,
  deleteProbe,
  devicePortOptions,
  editProbe,
} from "../../../helpers/wizard/probeReducer";
import { ConfirmAction } from "../../dashboard/ConfirmAction";
import { PortForm } from "./PortForm";
import "./probes.css";

export interface PortsCardProps {
  probeMap: ProbeMap;
  profiles: ProbeProfile[];
  onChange: (next: ProbeMap) => void;
}

interface FormState {
  mode: "add" | "edit";
  originalLabel: string;
  values: {
    name: string;
    device_port: string;
    type: ProbeMap["probe_info"][number]["type"];
    profile_id: string;
    enabled: string;
  };
}

const EMPTY: FormState["values"] = {
  name: "",
  device_port: "",
  type: "Food",
  profile_id: "",
  enabled: "true",
};

export function PortsCard({ probeMap, profiles, onChange }: PortsCardProps) {
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const options = devicePortOptions(probeMap);

  function openEdit(p: ProbeMap["probe_info"][number]) {
    setError(null);
    setForm({
      mode: "edit",
      originalLabel: p.label,
      values: {
        name: p.name,
        device_port: `${p.device}:${p.port}`,
        type: p.type,
        profile_id: "id" in p.profile ? p.profile.id : "",
        enabled: p.enabled ? "true" : "false",
      },
    });
  }

  function del(label: string) {
    const r = deleteProbe(probeMap, label);
    if (r.ok) {
      onChange(r.probeMap);
      setError(null);
    } else {
      setError(r.error);
    }
  }

  function submit() {
    if (!form) return;
    const input = {
      name: form.values.name,
      devicePort: form.values.device_port,
      type: form.values.type,
      profileId: form.values.profile_id,
      enabled: form.values.enabled === "true",
    };
    const r =
      form.mode === "add"
        ? addProbe(probeMap, profiles, input)
        : editProbe(probeMap, profiles, form.originalLabel, input);
    if (r.ok) {
      onChange(r.probeMap);
      setForm(null);
      setError(null);
    } else {
      setError(r.error);
    }
  }

  return (
    <section className="pf-probes-card" aria-label="Probe ports">
      <h3>Ports</h3>
      {/* Delete-guard errors surface here since Delete has no open form to
          host the alert (add/edit errors surface inside PortForm's dialog). */}
      {!form && error && <p role="alert">{error}</p>}
      <table className="pf-probes-table">
        <tbody>
          {probeMap.probe_info.map((p) => (
            <tr key={p.label}>
              <td>{p.name}</td>
              <td>{p.enabled ? "✓" : "✗"}</td>
              <td>{p.type}</td>
              <td>{p.device}</td>
              <td>{p.port}</td>
              <td>
                {p.port.includes("ADC") ? ((p.profile as { name?: string }).name ?? "") : "NA"}
              </td>
              <td>
                <button type="button" disabled={!!form} onClick={() => openEdit(p)}>
                  Edit
                </button>
                <button type="button" disabled={!!form} onClick={() => setPendingDelete(p.label)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {!form && (
        <button
          type="button"
          className="pf-btn"
          onClick={() => {
            setError(null);
            setForm({ mode: "add", originalLabel: "", values: { ...EMPTY } });
          }}
        >
          Add Probe
        </button>
      )}
      {form && (
        <PortForm
          mode={form.mode}
          devicePortOptions={options}
          profiles={profiles}
          values={form.values}
          error={error}
          onFieldChange={(field, value) =>
            setForm({ ...form, values: { ...form.values, [field]: value } })
          }
          onSubmit={submit}
          onCancel={() => {
            setForm(null);
            setError(null);
          }}
        />
      )}

      {/* Legacy asked before removing a probe too -- _macro_probes_config.html:354-360
          ("Delete Probe?"). `del` still owns the Primary-probe invariant, so a
          confirmed-but-rejected delete closes the dialog and surfaces the guard
          error in the alert slot above. */}
      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete Probe?"
        message="This probe will be removed from the configuration."
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete !== null) del(pendingDelete);
          setPendingDelete(null);
        }}
      />
    </section>
  );
}
