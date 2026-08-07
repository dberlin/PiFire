import { useState } from "react";
import { Link, useLoaderData, useOutletContext, useRevalidator } from "react-router";
import {
  applyProbeMap,
  readLiveProbeMap,
  readLiveProfiles,
} from "../../../helpers/probes/probeMapApi";
import type { ProbeModuleCatalog } from "../../../helpers/probes/probeMapTypes";
import { queryKeys } from "../../../helpers/query/keys";
import { queryClient } from "../../../helpers/query/queryClient";
import type { Settings } from "../../../helpers/settings/settingsApi";
import { useSettingsDraft } from "../../../helpers/settings/settingsDrafts";
import type { ProbeMap } from "../../../helpers/wizard/probeTypes";
import { DevicesCard } from "../../wizard/probes/DevicesCard";
import { PortsCard } from "../../wizard/probes/PortsCard";
import { Section } from "../fields/Section";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

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
  const { settings, mode } = useOutletContext<{ settings: Settings; mode: string }>();
  const catalog = useLoaderData() as ProbeModuleCatalog;
  const revalidator = useRevalidator();

  const live = readLiveProbeMap(settings);
  // Held on SettingsShell, so a half-built probe map survives a trip to another
  // tab -- the most expensive edit on this whole surface to have to redo.
  const {
    value: working,
    setValue: setWorking,
    markSaved,
    clear: discard,
  } = useSettingsDraft("probes", readLiveProbeMap);
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
  const dirty = JSON.stringify(working) !== JSON.stringify(live);
  const blocked = blockedModules(working, live, catalog.requires_install);
  const running = mode !== "Stop";
  const canSave = dirty && !running && blocked.length === 0 && !saving;

  const onSave = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);
    const r = await applyProbeMap(BASE_URL, working);
    setSaving(false);
    if (r.ok) {
      setSaved(true);
      markSaved(); // the draft is spent; the next loader result supersedes it
      // Same reason as useSaveSettings.ts: settingsLoader now primes itself
      // through fetchQuery, which serves a fresh cache entry unchanged, so
      // without invalidating first, revalidate() would re-run settingsLoader
      // AND probeModulesLoader but put the PRE-save probe map back on screen.
      await queryClient.invalidateQueries({ queryKey: queryKeys.settingsRoot });
      revalidator.revalidate(); // re-runs settingsLoader AND probeModulesLoader
    } else {
      // Deliberately keeps `working` -- the store is untouched on every
      // rejection path, so there is no drift to correct, and discarding the
      // user's edits exactly when they need to fix them is the worse bug.
      setError(r.message);
    }
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

        <DevicesCard
          probeMap={working}
          modules={catalog.modules}
          baseUrl={BASE_URL}
          onChange={setWorking}
        />
        <PortsCard probeMap={working} profiles={readLiveProfiles(settings)} onChange={setWorking} />

        <div className="pf-settings-actions">
          <button
            type="button"
            className="pf-modal-btn accent"
            disabled={!canSave}
            onClick={() => void onSave()}
          >
            {saving ? "Applying…" : "Save probe configuration"}
          </button>
          <button
            type="button"
            className="pf-modal-btn"
            disabled={!dirty || saving}
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
    </div>
  );
}
