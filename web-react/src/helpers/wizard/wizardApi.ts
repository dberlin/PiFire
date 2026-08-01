import type { BtScanRow, ProbeDevice, RowsResult, ThermoworksRow } from "./probeTypes";
import type {
  InstallLog,
  InstallStatus,
  ModuleValues,
  ScanResult,
  WizardSection,
  WizardState,
  WizardWorking,
} from "./wizardTypes";

function url(baseUrl: string, path: string): string {
  return `${baseUrl}/api/wizard/${path}`;
}

export async function getWizardState(baseUrl: string): Promise<WizardState> {
  const r = await fetch(url(baseUrl, "state"));
  return (await r.json()) as WizardState;
}

export async function saveDraft(baseUrl: string, working: WizardWorking): Promise<boolean> {
  const r = await fetch(url(baseUrl, "draft"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(working),
  });
  return r.ok;
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
    body: "{}",
  });
  return r.ok;
}

export async function scan(
  baseUrl: string,
  // vid/pid are passed through from the manifest as written ("0x2a19"); the
  // backend coerces them to the int pyserial reports. Typing them as `number`
  // here would have forced every caller to parse hex first, which is how they
  // ended up being dropped entirely.
  body: { kind: string; vid?: string | number | null; pid?: string | number | null },
): Promise<ScanResult> {
  const r = await fetch(url(baseUrl, "scan"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await r.json()) as ScanResult;
}

export async function fetchModuleValues(
  baseUrl: string,
  section: WizardSection,
  module: string,
): Promise<ModuleValues> {
  const r = await fetch(url(baseUrl, "module-values"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, module }),
  });
  if (!r.ok) throw new Error(`module-values failed: ${r.status}`);
  return (await r.json()) as ModuleValues;
}

export async function finishWizard(
  baseUrl: string,
  working: WizardWorking,
): Promise<{ ok: boolean; status: number; message?: string }> {
  const r = await fetch(url(baseUrl, "finish"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(working),
  });
  const body = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, message: body?.message };
}

export async function getInstallStatus(baseUrl: string): Promise<InstallStatus> {
  const r = await fetch(url(baseUrl, "installstatus"));
  return (await r.json()) as InstallStatus;
}

/** Everything the installer has logged since `offset` bytes. Offset 0 reads the
 *  whole of the current run, which is what makes opening the output panel late
 *  in an install show the install from its beginning. */
export async function getInstallLog(baseUrl: string, offset: number): Promise<InstallLog> {
  const r = await fetch(`${url(baseUrl, "installlog")}?offset=${offset}`);
  return (await r.json()) as InstallLog;
}

export async function scanBluetooth(baseUrl: string): Promise<RowsResult<BtScanRow>> {
  const r = await fetch(url(baseUrl, "scan/bluetooth"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  return (await r.json()) as RowsResult<BtScanRow>;
}

export async function scanThermoworks(
  baseUrl: string,
  email: string,
  password: string,
): Promise<RowsResult<ThermoworksRow>> {
  const r = await fetch(url(baseUrl, "scan/thermoworks"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return (await r.json()) as RowsResult<ThermoworksRow>;
}

export async function validateBusKinds(
  baseUrl: string,
  probeDevices: ProbeDevice[],
): Promise<{ ok: boolean; detail?: string }> {
  const r = await fetch(url(baseUrl, "probes/validate-bus-kinds"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ probe_devices: probeDevices }),
  });
  return (await r.json()) as { ok: boolean; detail?: string };
}
