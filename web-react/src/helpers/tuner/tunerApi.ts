// Typed client for the /api/tuner/* surface.
//
// Only openSession and closeSession write anything. fetchTr is polled once a
// second and is a pure GET by design -- see blueprints/api_tuner's docstring
// for why the session and the reading are separate endpoints.

import type { ApiEnvelope } from "../contracts/core.gen";
import type {
  AutoStatus,
  AutoStatusRequest,
  Coefficients,
  CoefficientsRequest,
  ProfileInput,
  SavedProfile,
  TrReading,
  TunerPoint,
  TunerSession,
  TunerSessionRequest,
} from "../contracts/operations.gen";
import type { TunerResult } from "./tunerTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const url = (baseUrl: string, path: string) => `${baseUrl}/api/tuner/${path}`;


async function unpack<T>(res: Response): Promise<TunerResult<T>> {
  const body = (await res.json().catch(() => ({}))) as Partial<ApiEnvelope>;
  const detail = (body.data ?? null) as (T & { mode?: string; field?: string }) | null;
  return {
    ok: res.ok && body.result === "OK",
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    data: detail,
    mode: detail?.mode,
    field: detail?.field,
  };
}

async function get<T>(baseUrl: string, path: string): Promise<TunerResult<T>> {
  try {
    return await unpack<T>(await fetch(url(baseUrl, path)));
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

async function post<T>(baseUrl: string, path: string, body: unknown): Promise<TunerResult<T>> {
  try {
    return await unpack<T>(
      await fetch(url(baseUrl, path), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

/** Turn a refusal into copy for a human. The server's `message` is a machine
 * token that tests/web assert on, so the translation lives here and nowhere
 * else -- no component matches on the token itself. */
export function tunerErrorText(result: TunerResult<unknown>): string {
  switch (result.message) {
    case "not_tunable":
      return `The grill must be stopped before tuning — it is currently in ${result.mode || "another"} mode.`;
    case "uncomputable":
      return "Those three readings could not be calculated into a profile. Check that each temperature and resistance pair is different from the others, then try again.";
    case "not_found":
      return "That probe is no longer configured.";
    case "bad_request":
      return result.field
        ? `The server refused that request: ${result.field}.`
        : "The server refused that request.";
    default:
      return result.message;
  }
}

/** Enter tuning mode. Moves a STOPPED grill to Monitor; refused with 409
 * `not_tunable` from any mode that is neither Stop nor Monitor. */
export const openSession = (baseUrl = BASE_URL) => {
  const body: TunerSessionRequest = { open: true };
  return post<TunerSession>(baseUrl, "session", body);
};

/** Leave tuning mode, restoring Stop only if the grill is still in Monitor.
 * Idempotent: the page closes on unmount, which can follow an explicit Finish. */
export const closeSession = (baseUrl = BASE_URL) => {
  const body: TunerSessionRequest = { open: false };
  return post<TunerSession>(baseUrl, "session", body);
};

/** One probe's live resistance. Inert — safe to poll. */
export const fetchTr = (probe: string, baseUrl = BASE_URL) =>
  get<TrReading>(baseUrl, `tr?probe=${encodeURIComponent(probe)}`);

/** Solve Steinhart-Hart. Refused with 422 `uncomputable` rather than
 * answering the (0, 0, 0) the underlying function returns on failure. */
export const computeCoefficients = (points: TunerPoint[], baseUrl = BASE_URL) => {
  const body: CoefficientsRequest = { points };
  return post<Coefficients>(baseUrl, "coefficients", body);
};

export const saveProfile = (profile: ProfileInput, baseUrl = BASE_URL) =>
  post<SavedProfile>(baseUrl, "profile", profile);

/** Record one auto-tune sample and read the running selection.
 *
 * A POST, not a GET: each poll captures a datapoint. It writes only the
 * autotune queue server-side, never control -- the session calls remain the
 * sole writers of grill state. */
export const fetchAutoStatus = (probe: string, reference: string, baseUrl = BASE_URL) => {
  const body: AutoStatusRequest = { probe, reference };
  return post<AutoStatus>(baseUrl, "auto-status", body);
};
