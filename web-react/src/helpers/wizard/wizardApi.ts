import type { BtScanRow, ProbeDevice, RowsResult, ThermoworksRow } from "./probeTypes";
import type { InstallStatus, ScanResult, WizardState, WizardWorking } from "./wizardTypes";

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

export async function scan(
  baseUrl: string,
  body: { kind: string; vid?: number; pid?: number },
): Promise<ScanResult> {
  const r = await fetch(url(baseUrl, "scan"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await r.json()) as ScanResult;
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
