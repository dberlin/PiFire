import type {
  BtRowsResult,
  BusKindsValidationRequest,
  BusKindsValidationResponse,
  EmptyWizardRequest,
  InstallLog,
  InstallStatus,
  ModuleValues,
  ModuleValuesRequest,
  ProbeDevice,
  ScanRequest,
  ScanResult,
  ThermoworksRequest,
  ThermoworksRowsResult,
  WizardDraftRequest,
  WizardFinishRequest,
  WizardSection,
  WizardState,
  WizardActionResponse,
} from "../contracts/wizard.gen";
import type { WizardWorking } from "./wizardTypes";

function url(baseUrl: string, path: string): string {
  return `${baseUrl}/api/wizard/${path}`;
}

export async function getWizardState(baseUrl: string): Promise<WizardState> {
  const r = await fetch(url(baseUrl, "state"));
  return r.json();
}

export async function saveDraft(baseUrl: string, working: WizardWorking): Promise<boolean> {
  const body: WizardDraftRequest = {
    selections: working.selections,
    settings_dep_values: working.settings_dep_values,
    display_config: working.display_config,
    probe_map: working.probe_map,
    probes_units: working.probes_units,
  };
  const r = await fetch(url(baseUrl, "draft"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const response: WizardActionResponse = await r.json();
  return r.ok && response.result === "success";
}

/** Leave the wizard without installing anything. The route clears
 * `globals.first_time_setup`, which is what stops DashboardRoute's post-mount
 * check from bouncing the user straight back to /wizard. Returns the response's
 * ok-ness rather than throwing: a false must keep the caller in the wizard,
 * because the flag is then still set and navigating away would loop. Leaves the
 * draft alone -- flush it with saveDraft() first if it should survive. */
export async function cancelWizard(baseUrl: string): Promise<boolean> {
  const r = await fetch(url(baseUrl, "cancel"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({} satisfies EmptyWizardRequest),
  });
  const response: WizardActionResponse = await r.json();
  return r.ok && response.result === "success";
}

export async function scan(baseUrl: string, body: ScanRequest): Promise<ScanResult> {
  const r = await fetch(url(baseUrl, "scan"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

export async function fetchModuleValues(
  baseUrl: string,
  section: WizardSection,
  module: string,
): Promise<ModuleValues> {
  const r = await fetch(url(baseUrl, "module-values"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, module } satisfies ModuleValuesRequest),
  });
  if (!r.ok) throw new Error(`module-values failed: ${r.status}`);
  return r.json();
}

export async function finishWizard(
  baseUrl: string,
  working: WizardWorking,
): Promise<{ ok: boolean; status: number; message?: string; detail?: string }> {
  const requestBody: WizardFinishRequest = {
    selections: working.selections,
    settings_dep_values: working.settings_dep_values,
    display_config: working.display_config,
    probe_map: working.probe_map,
    probes_units: working.probes_units,
  };
  const r = await fetch(url(baseUrl, "finish"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(requestBody),
  });
  const body: WizardActionResponse = await r.json();
  // `detail` rides along with bus_conflict: common/i2c_bus.py raises full
  // sentences naming the offending device and the values its bus kind accepts,
  // which no message keyed off the code alone can reconstruct.
  return { ok: r.ok && body.result === "success", status: r.status, message: body.message, detail: body.detail };
}

export async function getInstallStatus(baseUrl: string): Promise<InstallStatus> {
  const r = await fetch(url(baseUrl, "installstatus"));
  return r.json();
}

/** Everything the installer has logged since `offset` bytes. Offset 0 reads the
 * whole of the current run, which is what makes opening the output panel late
 * in an install show the install from its beginning. */
export async function getInstallLog(baseUrl: string, offset: number): Promise<InstallLog> {
  const r = await fetch(`${url(baseUrl, "installlog")}?offset=${offset}`);
  return r.json();
}

export async function scanBluetooth(baseUrl: string): Promise<BtRowsResult> {
  const r = await fetch(url(baseUrl, "scan/bluetooth"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({} satisfies EmptyWizardRequest),
  });
  return r.json();
}

export async function scanThermoworks(
  baseUrl: string,
  email: string,
  password: string,
): Promise<ThermoworksRowsResult> {
  const r = await fetch(url(baseUrl, "scan/thermoworks"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password } satisfies ThermoworksRequest),
  });
  return r.json();
}

export async function validateBusKinds(
  baseUrl: string,
  probeDevices: ProbeDevice[],
): Promise<BusKindsValidationResponse> {
  const r = await fetch(url(baseUrl, "probes/validate-bus-kinds"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ probe_devices: probeDevices } satisfies BusKindsValidationRequest),
  });
  return r.json();
}
