import type { ZodType } from "zod";

import {
  backendErrorSchema,
  chartSchema,
  decisionsPageSchema,
  healthSchema,
  snapshotSchema,
  tradesPageSchema,
} from "./schemas";

export type QueryValue = string | number | undefined;
export type Query = Readonly<Record<string, QueryValue>>;
export type DashboardApiErrorKind = "invalid-query" | "unavailable" | "malformed" | "network";

export class DashboardApiError extends Error {
  readonly kind: DashboardApiErrorKind;
  readonly status: number | null;
  readonly code: string;

  constructor(kind: DashboardApiErrorKind, message: string, code: string, status: number | null) {
    super(message);
    this.name = "DashboardApiError";
    this.kind = kind;
    this.code = code;
    this.status = status;
  }
}

function queryString(query: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new DashboardApiError(
      "malformed",
      "The service returned an unreadable response.",
      "MALFORMED_RESPONSE",
      response.status,
    );
  }
}

async function getValidated<T>(
  path: string,
  schema: ZodType<T>,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new DashboardApiError(
      "network",
      "The observation service could not be reached.",
      "NETWORK_FAILURE",
      null,
    );
  }
  const body = await readJson(response);
  if (!response.ok) {
    const structured = backendErrorSchema.safeParse(body);
    if (structured.success) {
      const kind = response.status === 400 ? "invalid-query" : "unavailable";
      throw new DashboardApiError(
        kind,
        structured.data.message,
        structured.data.code,
        response.status,
      );
    }
    throw new DashboardApiError(
      "malformed",
      "The service returned an unexpected error response.",
      "MALFORMED_ERROR_RESPONSE",
      response.status,
    );
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new DashboardApiError(
      "malformed",
      "The service response failed validation.",
      "MALFORMED_RESPONSE",
      response.status,
    );
  }
  return parsed.data;
}

export const dashboardApi = {
  health: (signal?: AbortSignal) => getValidated("/health", healthSchema, signal),
  snapshot: (signal?: AbortSignal) =>
    getValidated("/api/v1/dashboard/snapshot", snapshotSchema, signal),
  chart: (query: Query, signal?: AbortSignal) =>
    getValidated(`/api/v1/dashboard/chart${queryString(query)}`, chartSchema, signal),
  decisions: (query: Query, signal?: AbortSignal) =>
    getValidated(`/api/v1/dashboard/decisions${queryString(query)}`, decisionsPageSchema, signal),
  trades: (query: Query, signal?: AbortSignal) =>
    getValidated(`/api/v1/dashboard/trades${queryString(query)}`, tradesPageSchema, signal),
} as const;
