import type { ControllerCatalog } from "./controllerTypes.gen";
import type { SettingsSchema } from "./settingsTypes.gen";

/** The catalog's first controller — what an install falls back to when
 *  `controller.selected` names one this build does not ship. */
function firstControllerKey(meta: ControllerCatalog | null): string {
  if (!meta) return "";
  return Object.keys(meta.metadata)[0] ?? "";
}

/**
 * Which controller the grill is configured to run.
 *
 * Read from SAVED settings, never from the Controller tab's draft: the draft
 * store is shared across tabs, and an unsaved selection over there is not what
 * this grill is running, so nothing on another tab should silently follow it.
 *
 * One definition, shared by ControllerTab (which renders the selected
 * controller's options) and WorkModeTab (which offers its recommended u_max),
 * because the two tabs must agree on what "selected" means.
 */
export function readSelected(settings: SettingsSchema, meta: ControllerCatalog | null): string {
  const sel = settings.controller?.selected;
  if (typeof sel === "string" && meta?.metadata[sel]) return sel;
  return firstControllerKey(meta);
}
