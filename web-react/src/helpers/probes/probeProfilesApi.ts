import type {
  ProbeProfileMutation,
  ProfileInput,
  ProfileValues,
  SavedProfile,
} from "@pifire/core/contracts/operations";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

type ProfileResult<T> = { ok: true; data: T } | { ok: false; message: string };

function explain(message: unknown, data: unknown, status: number): string {
  const detail =
    typeof data === "object" && data !== null && "detail" in data && typeof data.detail === "string"
      ? data.detail
      : null;
  switch (message) {
    case "system_active":
      return "Stop the grill before changing a profile assigned to a probe. Your changes have not been saved.";
    case "profile_in_use":
      return "This profile is assigned to a configured probe, including disabled probes. Choose another profile for every reference in Settings → Probes and save that configuration before deleting it.";
    case "not_found":
      return "This profile no longer exists. Discard this draft and choose an existing profile or add a new one.";
    case "bad_request": {
      const field =
        typeof data === "object" &&
        data !== null &&
        "field" in data &&
        typeof data.field === "string"
          ? data.field
          : null;
      return (
        detail ??
        (field ? `Check ${field} and try again.` : "Check the name and coefficients and try again.")
      );
    }
    default:
      return detail ?? `The profile change was not saved (HTTP ${status}). Please try again.`;
  }
}

function isSavedProfile(value: unknown): value is SavedProfile {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "string" &&
    "applied" in value &&
    (value.applied === null || typeof value.applied === "string")
  );
}

function isProfileMutation(value: unknown): value is ProbeProfileMutation {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "string" &&
    "updated_probes" in value &&
    Array.isArray(value.updated_probes) &&
    value.updated_probes.every((probe: unknown) => typeof probe === "string")
  );
}

async function mutate<T>(
  request: () => Promise<Response>,
  isData: (value: unknown) => value is T,
): Promise<ProfileResult<T>> {
  try {
    const response = await request();
    const body: unknown = await response.json().catch(() => null);
    if (typeof body !== "object" || body === null || !("result" in body)) {
      return {
        ok: false,
        message:
          "PiFire returned an unreadable response. Refresh settings before retrying the change.",
      };
    }
    const data = "data" in body ? body.data : null;
    if (response.ok && body.result === "OK" && isData(data)) return { ok: true, data };
    return {
      ok: false,
      message: explain("message" in body ? body.message : null, data, response.status),
    };
  } catch {
    return {
      ok: false,
      message:
        "Could not reach PiFire. Your draft is still here; check the connection and refresh settings before retrying.",
    };
  }
}

export function createProbeProfile(profile: ProfileInput, baseUrl = BASE_URL) {
  return mutate(
    () =>
      fetch(`${baseUrl}/api/probe_profiles`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      }),
    isSavedProfile,
  );
}

export function updateProbeProfile(id: string, profile: ProfileValues, baseUrl = BASE_URL) {
  return mutate(
    () =>
      fetch(`${baseUrl}/api/probe_profiles/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      }),
    isProfileMutation,
  );
}

export function deleteProbeProfile(id: string, baseUrl = BASE_URL) {
  return mutate(
    () => fetch(`${baseUrl}/api/probe_profiles/${encodeURIComponent(id)}`, { method: "DELETE" }),
    isProfileMutation,
  );
}
