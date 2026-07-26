import type { ProbeModuleData } from "../wizard/probeTypes";

/** GET /api/probe_modules -> body.data (blueprints/api/routes.py). Both maps
 *  are keyed by module name; `requires_install` is true when the module
 *  declares py/apt/command dependencies, i.e. adding it needs the wizard's
 *  installer (wizard.py:319-430) and POST /api/probe_map will refuse it. */
export interface ProbeModuleCatalog {
  modules: Record<string, ProbeModuleData>;
  requires_install: Record<string, boolean>;
}

/** POST /api/probe_map. `message` is already user-facing: the route's four
 *  rejection codes are translated in probeMapApi, not in the component. */
export type ApplyProbeMapResult = { ok: true } | { ok: false; message: string };
