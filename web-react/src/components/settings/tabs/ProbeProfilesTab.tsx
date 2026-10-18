import type { ProfileValues } from "@pifire/core/contracts/operations";
import type { ProbeProfile } from "@pifire/core/contracts/wizard";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useOutletContext, useRevalidator } from "react-router";

import { readLiveProfiles } from "../../../helpers/probes/probeMapApi";
import {
  createProbeProfile,
  deleteProbeProfile,
  updateProbeProfile,
} from "../../../helpers/probes/probeProfilesApi";
import { queryKeys } from "../../../helpers/query/keys";
import {
  type SettingsDraftContext,
  useSettingsDraft,
} from "../../../helpers/settings/settingsDrafts";
import { ConfirmAction } from "../../dashboard/ConfirmAction";
import { Section } from "../fields/Section";
import { TextField } from "../fields/TextField";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";
const COEFFICIENTS = ["a", "b", "c"] as const;

interface ProfileDraft {
  id: string | null;
  name: string;
  a: string;
  b: string;
  c: string;
  pending: boolean;
  error: string | null;
}

function noDraft(): ProfileDraft | null {
  return null;
}

// Usage depends on stable IDs, not on whether a legacy probe's other fields
// satisfy the current wizard schema. Disabled probes still own a reference.
function profileUsage(settings: SettingsSchema, id: string): string[] {
  const names: string[] = [];
  for (const probe of settings.probe_settings?.probe_map?.probe_info ?? []) {
    const profile = probe.profile;
    if (
      typeof profile !== "object" ||
      profile === null ||
      !("id" in profile) ||
      profile.id !== id
    ) {
      continue;
    }
    const name =
      typeof probe.name === "string" && probe.name.trim()
        ? probe.name
        : typeof probe.label === "string" && probe.label.trim()
          ? probe.label
          : "Unnamed probe";
    const label =
      typeof probe.label === "string" && probe.label !== name ? ` (${probe.label})` : "";
    names.push(`${name}${label}${probe.enabled === false ? " (disabled)" : ""}`);
  }
  return names;
}

function coefficientError(value: string): string | null {
  if (!value.trim()) return "Enter a coefficient; blank is not zero.";
  return Number.isFinite(Number(value)) ? null : "Enter a finite number, such as 9.51568577e-8.";
}

export function ProbeProfilesTab() {
  const { settings, mode, setDrafts } = useOutletContext<SettingsDraftContext & { mode: string }>();
  const queryClient = useQueryClient();
  const revalidator = useRevalidator();
  const {
    value: draft,
    setValue: setDraft,
    clear: discard,
  } = useSettingsDraft("probe-profiles", noDraft);
  const [deleting, setDeleting] = useState(false);
  const saving = deleting || draft?.pending === true;
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ProbeProfile | null>(null);

  const profiles = readLiveProfiles(settings);
  const draftUsage = draft?.id ? profileUsage(settings, draft.id) : [];
  const editingBlocked = draftUsage.length > 0 && mode !== "Stop";
  const missingProfile =
    draft?.id !== null && draft !== null && !profiles.some((profile) => profile.id === draft.id);
  const nameError = draft && !draft.name.trim() ? "Enter a profile name." : null;

  function startEditing(profile?: ProbeProfile) {
    setDraft({
      id: profile?.id ?? null,
      name: profile?.name ?? "",
      a: profile ? String(profile.A) : "",
      b: profile ? String(profile.B) : "",
      c: profile ? String(profile.C) : "",
      pending: false,
      error: null,
    });
    setAttempted(false);
    setError(null);
    setSaved(null);
  }

  function discardDraft() {
    discard();
    setAttempted(false);
    setError(null);
  }

  async function refreshSettings() {
    await queryClient.invalidateQueries({ queryKey: queryKeys.settingsRoot(BASE_URL) });
    await revalidator.revalidate();
  }
  // The shared slot outlives this component. A response owns only the exact
  // submitted draft, never a new edit created after navigation or refresh.
  function finishDraft(submitted: ProfileDraft, failure: string | null) {
    setDrafts((drafts) => {
      if (drafts["probe-profiles"]?.value !== submitted) return drafts;
      if (failure !== null) {
        return {
          ...drafts,
          "probe-profiles": {
            value: { ...submitted, pending: false, error: failure },
            saved: false,
          },
        };
      }
      const { "probe-profiles": _finished, ...remaining } = drafts;
      return remaining;
    });
  }

  async function save() {
    if (!draft || saving || editingBlocked || missingProfile) return;
    setAttempted(true);
    setError(null);
    if (nameError || COEFFICIENTS.some((key) => coefficientError(draft[key]))) return;
    const values: ProfileValues = {
      name: draft.name.trim(),
      a: Number(draft.a),
      b: Number(draft.b),
      c: Number(draft.c),
    };
    const submitted = { ...draft, pending: true, error: null };
    setDraft(submitted);
    setSaved(null);
    const result =
      draft.id === null
        ? await createProbeProfile(values, BASE_URL)
        : await updateProbeProfile(draft.id, values, BASE_URL);
    if (!result.ok) {
      finishDraft(submitted, result.message);
      return;
    }
    try {
      await refreshSettings();
    } finally {
      finishDraft(submitted, null);
    }
    setSaved(`Saved ${values.name}.`);
  }

  async function remove(profile: ProbeProfile) {
    setPendingDelete(null);
    if (saving) return;
    if (profileUsage(settings, profile.id).length > 0) {
      setError(
        "This profile is now assigned to a probe. Choose another profile in Probes and save that configuration before deleting it.",
      );
      return;
    }
    setDeleting(true);
    setError(null);
    setSaved(null);
    const result = await deleteProbeProfile(profile.id, BASE_URL);
    if (!result.ok) {
      setError(result.message);
      setDeleting(false);
      return;
    }
    await refreshSettings();
    setSaved(`Deleted ${profile.name}.`);
    setDeleting(false);
  }

  return (
    <div className="pf-probe-profiles">
      <Section title="Probe Profiles">
        <p className="pf-settings-hint">
          Manage thermistor calibration profiles. Coefficients are shown without rounding. Assign
          profiles to probes in <Link to="/settings/probes">Probes</Link>.
        </p>
        {(draft?.error || error) && (
          <p className="pf-settings-error-text" role="alert">
            {draft?.error || error}
          </p>
        )}
        {saved && (
          <p className="pf-settings-saved" role="status">
            {saved}
          </p>
        )}
        <div className="pf-settings-actions">
          <button
            type="button"
            className="pf-modal-btn accent"
            disabled={saving || draft !== null}
            onClick={() => startEditing()}
          >
            Add profile
          </button>
          {draft && (
            <span className="pf-settings-hint">
              Save or discard the open draft before choosing another profile.
            </span>
          )}
        </div>
      </Section>

      {draft && (
        <Section title={draft.id === null ? "Add profile" : "Edit profile"}>
          {editingBlocked && (
            <p role="alert">
              The grill is running ({mode}). Stop it before editing this assigned profile.
            </p>
          )}
          {missingProfile && (
            <p role="alert">
              This profile no longer exists. Discard this draft to choose another profile or add a
              new one.
            </p>
          )}
          {draftUsage.length > 0 && (
            <p className="pf-settings-hint">
              Saving updates every assigned probe: {draftUsage.join(", ")}.
            </p>
          )}
          <form
            className="pf-profile-form"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <fieldset disabled={saving || editingBlocked || missingProfile}>
              <legend className="pf-settings-hint">Name and Steinhart–Hart coefficients</legend>
              <TextField
                label="Name"
                value={draft.name}
                onChange={(name) => setDraft({ ...draft, name, error: null })}
                error={attempted ? nameError : null}
              />
              {COEFFICIENTS.map((key) => (
                <TextField
                  key={key}
                  label={key.toUpperCase()}
                  value={draft[key]}
                  onChange={(value) => setDraft({ ...draft, [key]: value, error: null })}
                  error={attempted ? coefficientError(draft[key]) : null}
                />
              ))}
              <p className="pf-settings-hint">
                Enter finite numbers, including scientific notation (for example, 9.51568577e-8).
                Zero and negative coefficients are allowed.
              </p>
            </fieldset>
            <div className="pf-settings-actions">
              <button
                type="submit"
                className="pf-modal-btn accent"
                disabled={saving || editingBlocked || missingProfile}
              >
                {saving ? "Saving…" : "Save profile"}
              </button>
              <button
                type="button"
                className="pf-modal-btn"
                disabled={saving}
                onClick={discardDraft}
              >
                Discard changes
              </button>
            </div>
          </form>
        </Section>
      )}

      <Section title="Saved profiles">
        {profiles.length === 0 ? (
          <p>No probe profiles have been saved.</p>
        ) : (
          <ul className="pf-profile-list">
            {profiles.map((profile) => {
              const usage = profileUsage(settings, profile.id);
              const inUse = usage.length > 0;
              const locked = inUse && mode !== "Stop";
              return (
                <li key={profile.id} className="pf-profile-card">
                  <h3>{profile.name}</h3>
                  <dl className="pf-profile-coefficients">
                    {(["A", "B", "C"] as const).map((key) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd>
                          <code>{String(profile[key])}</code>
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p className="pf-settings-hint">
                    {inUse ? `Used by: ${usage.join(", ")}.` : "Not assigned to any probe."}
                  </p>
                  {inUse && (
                    <p className="pf-settings-hint">
                      To delete, choose another profile for these probes in Probes and save the
                      configuration, including disabled probes.
                    </p>
                  )}
                  {locked && (
                    <p className="pf-settings-hint">
                      Stop the grill to edit this assigned profile.
                    </p>
                  )}
                  <div className="pf-settings-actions">
                    <button
                      type="button"
                      className="pf-modal-btn"
                      aria-label={`Edit ${profile.name}`}
                      disabled={saving || draft !== null || locked}
                      onClick={() => startEditing(profile)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="pf-modal-btn danger"
                      aria-label={`Delete ${profile.name}`}
                      disabled={saving || draft !== null || inUse}
                      onClick={() => setPendingDelete(profile)}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Section>
      <ConfirmAction
        open={pendingDelete !== null}
        viewport
        title={`Delete profile ${pendingDelete?.name ?? ""}?`}
        message="This permanently removes the unused profile from your library."
        confirmLabel="Delete profile"
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) void remove(pendingDelete);
        }}
      />
    </div>
  );
}
