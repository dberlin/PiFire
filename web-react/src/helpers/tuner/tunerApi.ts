// Typed client for the /api/tuner/* surface.
//
// Only openSession and closeSession write anything. fetchTr is polled once a
// second and is a pure GET by design -- see blueprints/api_tuner's docstring
// for why the session and the reading are separate endpoints.

import type {
  Coefficients,
  ProfileInput,
  SavedProfile,
  TrReading,
  TunerPoint,
  TunerResult,
  TunerSession,
} from "./tunerTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const url = (baseUrl: string, path: string) => `${baseUrl}/api/tuner/${path}`;

async function unpack<T>(res: Response): Promise<TunerResult<T>> {
  const body = (await res.json().catch(() => ({}))) as {
    result?: string;
    message?: string;
    data?: (T & { mode?: string; field?: string }) | null;
  };
  const detail = body.data ?? null;
  return {
    ok: res.ok && body.result === "OK",
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    data: detail as T | null,
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
export const openSession = (baseUrl = BASE_URL) =>
  post<TunerSession>(baseUrl, "session", { open: true });

/** Leave tuning mode, restoring Stop only if the grill is still in Monitor.
 * Idempotent: the page closes on unmount, which can follow an explicit Finish. */
export const closeSession = (baseUrl = BASE_URL) =>
  post<TunerSession>(baseUrl, "session", { open: false });

/** One probe's live resistance. Inert — safe to poll. */
export const fetchTr = (probe: string, baseUrl = BASE_URL) =>
  get<TrReading>(baseUrl, `tr?probe=${encodeURIComponent(probe)}`);

/** Solve Steinhart-Hart. Refused with 422 `uncomputable` rather than
 * answering the (0, 0, 0) the underlying function returns on failure. */
export const computeCoefficients = (points: TunerPoint[], baseUrl = BASE_URL) =>
  post<Coefficients>(baseUrl, "coefficients", { points });

export const saveProfile = (profile: ProfileInput, baseUrl = BASE_URL) =>
  post<SavedProfile>(baseUrl, "profile", profile);
