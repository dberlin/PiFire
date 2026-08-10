// Shared envelope helpers for the /api/files/* surface, parameterised by
// FileKind so cookfileApi.ts and recipeApi.ts do not each carry their own
// copy of the fetch/error plumbing.
//
// Write responses use common/app.py's api_response envelope
// {data, result, message} with result === "OK" on success. Read responses
// are bare payloads with an HTTP status.

import type { ContentErrorEnvelope, FileErrorDetail } from "../contracts/content.gen";
import type { ApiEnvelope } from "../contracts/core.gen";
import type { FileKind } from "./fileTypes";

export class FileRequestError extends Error {
  readonly detail: FileErrorDetail;
  constructor(detail: FileErrorDetail) {
    super(detail.message);
    this.name = "FileRequestError";
    this.detail = detail;
  }
}

export async function toError(res: Response): Promise<FileErrorDetail> {
  //  A 404 from a proxy (or an HTML error page) is not JSON. Never let a parse
  //  failure mask the status the caller has to branch on.
  const body = (await res.json().catch(() => ({}))) as Partial<ContentErrorEnvelope>;
  return {
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    errortype: body.data?.errortype ?? null,
  };
}

export async function read<T>(
  kind: FileKind,
  path: string,
  file: string,
  baseUrl: string,
): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/${kind}/${path}?file=${encodeURIComponent(file)}`);
  if (res.ok) return (await res.json()) as T;
  throw new FileRequestError(await toError(res));
}

/** POST helper for every JSON write endpoint. */
export async function write<T>(
  kind: FileKind,
  path: string,
  body: unknown,
  baseUrl: string,
): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/${kind}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new FileRequestError(await toError(res));
  const envelope = (await res.json()) as Partial<ApiEnvelope>;
  if (envelope.result !== "OK") {
    throw new FileRequestError({
      status: res.status,
      message: envelope.message ?? "rejected",
      errortype: null,
    });
  }
  return envelope.data as T;
}

/** Shared tail for multipart uploads, which cannot go through write():
 * the body is FormData, so no JSON Content-Type may be set. */
export async function postForm<T>(
  kind: FileKind,
  path: string,
  form: FormData,
  baseUrl: string,
): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/${kind}/${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new FileRequestError(await toError(res));
  const envelope = (await res.json()) as Partial<ApiEnvelope>;
  if (envelope.result !== "OK") {
    throw new FileRequestError({
      status: res.status,
      message: envelope.message ?? "rejected",
      errortype: null,
    });
  }
  return envelope.data as T;
}
