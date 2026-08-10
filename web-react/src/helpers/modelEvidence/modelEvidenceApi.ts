import type {
  ModelActivationAcknowledgement,
  ModelActivationRequest,
  ModelEvidenceReport,
  ModelEvidenceResult,
  ModelRollbackAcknowledgement,
  ModelRollbackRequest,
  MpcCalibrationCommand,
  MpcCalibrationRequest,
} from "./types";

const DEFAULT_BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const endpoint = (baseUrl: string, path: string) => `${baseUrl}/api/${path}`;

async function responseMessage(response: Response): Promise<string> {
  const body = (await response.json().catch(() => ({}))) as {
    message?: string;
    error?: string;
    detail?: string;
  };
  return body.detail ?? body.message ?? body.error ?? `HTTP ${response.status}`;
}

/** Read-only confidence projection. An empty ledger is still a successful collecting report. */
export async function fetchModelEvidenceReport(
  baseUrl = DEFAULT_BASE_URL,
  signal?: AbortSignal,
): Promise<ModelEvidenceResult<ModelEvidenceReport>> {
  try {
    const response = await fetch(endpoint(baseUrl, "model-evidence/report"), { signal });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseMessage(response),
        data: null,
      };
    }
    return {
      ok: true,
      status: response.status,
      message: "",
      data: (await response.json()) as ModelEvidenceReport,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}

/** Canonical sorted-key UTF-8 JSON bytes. Kept as bytes so the client cannot reserialize them. */
export async function fetchModelEvidenceArtifact(
  baseUrl = DEFAULT_BASE_URL,
): Promise<ModelEvidenceResult<Uint8Array>> {
  try {
    const response = await fetch(endpoint(baseUrl, "model-evidence/artifact"));
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseMessage(response),
        data: null,
      };
    }
    return {
      ok: true,
      status: response.status,
      message: "",
      data: new Uint8Array(await response.arrayBuffer()),
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}

async function postModelAction<
  TRequest,
  TResponse extends {
    accepted: boolean;
    detail?: string | null;
  },
>(
  path: string,
  request: TRequest,
  baseUrl: string,
): Promise<ModelEvidenceResult<TResponse>> {
  try {
    const response = await fetch(endpoint(baseUrl, path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const body = (await response.json().catch(() => null)) as
      | (Partial<TResponse> & {
          message?: string;
          error?: string;
          detail?: string | null;
        })
      | null;
    const acknowledgement =
      body !== null && typeof body.accepted === "boolean" ? (body as TResponse) : null;
    const ok = response.ok && acknowledgement?.accepted === true;
    return {
      ok,
      status: response.status,
      message: ok
        ? ""
        : (body?.detail ??
          body?.message ??
          body?.error ??
          (acknowledgement === null ? "Invalid action response" : `HTTP ${response.status}`)),
      data: acknowledgement,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}

/** Activate only the exact candidate digest and confidence decision the operator reviewed. */
export function activateModel(
  request: ModelActivationRequest,
  baseUrl = DEFAULT_BASE_URL,
): Promise<ModelEvidenceResult<ModelActivationAcknowledgement>> {
  return postModelAction("model-evidence/activate", request, baseUrl);
}

/** Roll back only when the unified report names an explicit rollback owner. */
export function rollbackModel(
  request: ModelRollbackRequest,
  baseUrl = DEFAULT_BASE_URL,
): Promise<ModelEvidenceResult<ModelRollbackAcknowledgement>> {
  return postModelAction("model-evidence/rollback", request, baseUrl);
}

/** Send one revisioned calibration intent. */
export async function setMpcCalibration(
  request: MpcCalibrationRequest,
  baseUrl = DEFAULT_BASE_URL,
): Promise<ModelEvidenceResult<MpcCalibrationCommand>> {
  const command: MpcCalibrationCommand = {
    action: request.action,
    revision: request.revision,
    ambient_c: request.ambient_c,
    ambient_source: request.ambient_source,
    empty_grill_confirmed: request.empty_grill_confirmed,
    pellets_confirmed: request.pellets_confirmed,
  };

  try {
    const response = await fetch(endpoint(baseUrl, "set_mpc_calibration"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    });
    const body = (await response.json().catch(() => ({}))) as {
      result?: string;
      message?: string;
      data?: { mpc_calibration?: MpcCalibrationCommand };
    };
    const ok = response.ok && body.result?.toUpperCase() === "OK";
    return {
      ok,
      status: response.status,
      message: body.message ?? `HTTP ${response.status}`,
      data: ok ? (body.data?.mpc_calibration ?? command) : null,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}
