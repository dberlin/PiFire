import type { ThermocoupleHealthView } from "@pifire/core/contracts/core";
import type { ProbeMap, ProbeModuleCatalog } from "@pifire/core/contracts/wizard";
import { projectProbeHealth } from "@pifire/core/dashboard/probeHealth";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLoaderData, useOutletContext, useRevalidator } from "react-router";

import {
  applyProbeMap,
  readLiveProbeMap,
  readLiveProfiles,
} from "../../../helpers/probes/probeMapApi";
import { queryKeys } from "../../../helpers/query/keys";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import { useSaveSettings } from "../../../helpers/settings/useSaveSettings";
import type { ConnectionPhase } from "../../../helpers/useLiveState";
import { DevicesCard } from "../../wizard/probes/DevicesCard";
import { PortsCard } from "../../wizard/probes/PortsCard";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

type InferencePolicy = NonNullable<
  NonNullable<SettingsSchema["thermocouple_health"]>["inference_policy"]
>;

interface ProbesDraft {
  probeMap: ProbeMap;
  policy: InferencePolicy;
}

const POLICY_OPTIONS: { value: InferencePolicy; label: string }[] = [
  { value: "off", label: "Off — hardware detection only" },
  { value: "observe", label: "Observe — report without stopping" },
  { value: "enforce", label: "Enforce — stop on a confirmed control-probe fault" },
];

const POLICY_IMPACT: Readonly<Record<InferencePolicy, string>> = {
  off: "Software thermocouple detection is disabled; only supported hardware can report faults.",
  observe: "Reports confirmed software-detected faults without stopping heating.",
  enforce: "Stops heating when the control probe has a confirmed fault.",
};

function readProbesDraft(settings: SettingsSchema): ProbesDraft {
  return {
    probeMap: readLiveProbeMap(settings),
    policy: settings.thermocouple_health?.inference_policy ?? "observe",
  };
}

function displayDetailValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

/** Modules being ADDED that the running system cannot install. Mirrors
 *  blueprints/api/probe_map_actions.py::unsupported_new_modules exactly -- the
 *  server is authoritative and will 422; this is the same rule, evaluated
 *  early, so the user learns before losing an edit. Computed during render;
 *  there is no state here and no effect. */
function blockedModules(
  working: ProbeMap,
  live: ProbeMap,
  requiresInstall: Record<string, boolean>,
): string[] {
  const installed = new Set(live.probe_devices.map((d) => d.module));
  const blocked = new Set<string>();
  for (const d of working.probe_devices) {
    // `!== false`, not `=== true`: a module missing from the catalog entirely --
    // a stale or renamed one in a saved map -- must count as blocked, matching
    // the server's module_requires_install(None) -> True.
    if (!installed.has(d.module) && requiresInstall[d.module] !== false) blocked.add(d.module);
  }
  return [...blocked].sort();
}

export function ProbesTab() {
  const {
    settings,
    mode,
    thermocoupleHealth = [],
    phase,
  } = useOutletContext<{
    settings: SettingsSchema;
    mode: string;
    thermocoupleHealth?: readonly ThermocoupleHealthView[];
    phase?: ConnectionPhase;
  }>();
  const catalog = useLoaderData<ProbeModuleCatalog>();
  const revalidator = useRevalidator();
  const queryClient = useQueryClient();
  const { save: saveSettings, saving: savingSettings, status: policyStatus } = useSaveSettings();

  const live = readProbesDraft(settings);
  // Held on SettingsShell, so policy and a half-built probe map survive a trip
  // to another tab as one coherent thermocouple configuration draft.
  const {
    value: working,
    setValue: setWorking,
    markSaved,
    clear: discard,
  } = useSettingsDraft("probes", readProbesDraft);
  const [prev, setPrev] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Render-phase adjustment, NOT an effect: the React Compiler is active and
  // setState-in-useEffect for derived state is banned. Same idiom as
  // SafetyTab.tsx:36-40. The map itself needs no re-sync -- the draft store
  // drops an applied draft when fresh settings arrive -- but the last
  // rejection message is about the map that has just been superseded.
  if (settings !== prev) {
    setPrev(settings);
    setError(null);
  }

  // JSON compare, not identity: the reducer returns fresh objects on every
  // edit, so a no-op round trip would otherwise read as dirty forever.
  const probeMapDirty = JSON.stringify(working.probeMap) !== JSON.stringify(live.probeMap);
  const policyDirty = working.policy !== live.policy;
  const dirty = probeMapDirty || policyDirty;
  const blocked = blockedModules(working.probeMap, live.probeMap, catalog.requires_install);
  const running = mode !== "Stop";
  const canSave =
    dirty && (!probeMapDirty || (!running && blocked.length === 0)) && !saving && !savingSettings;

  const onSave = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);

    if (
      policyDirty &&
      !(await saveSettings({ thermocouple_health: { inference_policy: working.policy } }, []))
    ) {
      setSaving(false);
      return;
    }

    if (probeMapDirty) {
      const result = await applyProbeMap(BASE_URL, working.probeMap);
      if (!result.ok) {
        setSaving(false);
        // Keep the whole draft. If the policy write already succeeded, fresh
        // loader data makes only that half clean while this rejected map stays.
        setError(result.message);
        return;
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.settingsRoot(BASE_URL) });
      revalidator.revalidate();
    }

    setSaving(false);
    setSaved(true);
    markSaved();
  };

  return (
    <div className="pf-probes-surface">
      <Section title="Probes">
        {running && (
          <p role="alert">
            The grill is running ({mode}). Stop it before changing probe configuration.
          </p>
        )}
        {blocked.length > 0 && (
          <p role="alert">
            These probe modules need the setup wizard to install their dependencies first:{" "}
            {blocked.join(", ")}. Remove them here, or run the wizard to add them.
          </p>
        )}
        {error && <p role="alert">{error}</p>}
        {policyStatus.kind === "error" ? <p role="alert">{policyStatus.message}</p> : null}

        <Select
          label="Software thermocouple detection"
          value={working.policy}
          options={POLICY_OPTIONS}
          onChange={(value) =>
            setWorking((draft) => ({
              ...draft,
              policy: value as InferencePolicy,
            }))
          }
          path="thermocouple_health.inference_policy"
        />
        <p className="pf-settings-hint">{POLICY_IMPACT[working.policy]}</p>

        {/* Devices and Ports sit side by side above 1000px (probes.css). The
            wrapper is what owns those two columns -- .pf-section-body's own
            grid is the shared label/control/unit subgrid and cannot be
            retargeted here without breaking every field on the tab. */}
        <div className="pf-probes-split">
          <DevicesCard
            probeMap={working.probeMap}
            modules={catalog.modules}
            baseUrl={BASE_URL}
            onChange={(probeMap) => setWorking((draft) => ({ ...draft, probeMap }))}
          />
          <PortsCard
            probeMap={working.probeMap}
            profiles={readLiveProfiles(settings)}
            onChange={(probeMap) => setWorking((draft) => ({ ...draft, probeMap }))}
          />
        </div>

        <div className="pf-settings-actions">
          <button
            type="button"
            className="pf-modal-btn accent"
            disabled={!canSave}
            onClick={() => void onSave()}
          >
            {saving || savingSettings ? "Applying…" : "Save probe configuration"}
          </button>
          <button
            type="button"
            className="pf-modal-btn"
            disabled={!dirty || saving || savingSettings}
            onClick={discard}
          >
            Discard changes
          </button>
          {saved && !dirty && <span className="pf-settings-saved">Applied ✓</span>}
          {/* The probe TUNER lives on its own top-level route, not under
              settings -- it opens a live tuning session and moves the grill
              into Monitor, which a settings tab should not. Flask reaches it
              only from base.html's navbar; the React navbar has no Tuner
              entry, so this link is the way in. */}
          <Link className="pf-modal-btn" to="/tuner">
            Tune a probe
          </Link>
        </div>
      </Section>

      {/* Folded away when there is nothing to report, which is what lets the
          default map fit 1280x720 without a scrollbar: the empty card is 95px
          of heading over one sentence.

          The condition is the REPORT COUNT and nothing else, so this cannot
          hide a fault -- a faulted probe is a report, and any report at all
          renders the card. It never collapses a card that has content, and
          there is no default-collapsed state to expand. Pinned by
          ProbesTab.test.tsx ("keeps the health card ... whenever anything is
          reported"). */}
      {thermocoupleHealth.length > 0 && (
        <section className="pf-probe-health-details" aria-label="Thermocouple health">
          <h2>Thermocouple health</h2>
          {thermocoupleHealth.map((item) => {
            const health = projectProbeHealth(item);
            const stateCopy = health.state
              .replaceAll("_", " ")
              .replace(/(^| )\S/g, (letter) => letter.toUpperCase());
            const status = health.headline ?? stateCopy;
            const retainedHealth = phase === "unreachable" || health.freshnessQualifier !== null;
            return (
              <article
                className={`pf-probe-health-detail pf-probe-health-detail--${health.severity}`}
                key={`${health.device}\u0000${health.port}\u0000${health.role}\u0000${health.label}`}
              >
                <h3>
                  {health.role} · {health.displayName}
                </h3>
                <p className="pf-probe-health-detail-status">
                  {retainedHealth ? "Last reported: " : null}
                  {status}
                </p>
                {health.impactCopy !== null ? <p>{health.impactCopy}</p> : null}
                {health.causeCopy !== null ? <p>{health.causeCopy}</p> : null}
                <dl>
                  <div>
                    <dt>Device</dt>
                    <dd>{health.device}</dd>
                  </div>
                  <div>
                    <dt>Port</dt>
                    <dd>{health.port}</dd>
                  </div>
                  <div>
                    <dt>State</dt>
                    <dd>{stateCopy}</dd>
                  </div>
                  <div>
                    <dt>Source</dt>
                    <dd>{health.sourceCopy}</dd>
                  </div>
                  <div>
                    <dt>Policy</dt>
                    <dd>
                      {health.policy
                        .replaceAll("_", " ")
                        .replace(/(^| )\S/g, (letter) => letter.toUpperCase())}
                    </dd>
                  </div>
                  <div>
                    <dt>Outcome</dt>
                    <dd>
                      {health.outcome
                        .replaceAll("_", " ")
                        .replace(/(^| )\S/g, (letter) => letter.toUpperCase())}
                    </dd>
                  </div>
                  <div>
                    <dt>Faults</dt>
                    <dd>{health.faults.length > 0 ? health.faults.join(", ") : "None"}</dd>
                  </div>
                  <div>
                    <dt>Evidence</dt>
                    <dd>{health.evidence.length > 0 ? health.evidence.join(", ") : "None"}</dd>
                  </div>
                  <div>
                    <dt>{phase === "unreachable" ? "Report age at last update" : "Report age"}</dt>
                    <dd>{Math.round(health.lastReportedAgeS)}s</dd>
                  </div>
                  {Object.entries(item.report.detail).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key.replaceAll("_", " ")}: </dt>
                      <dd>{displayDetailValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
