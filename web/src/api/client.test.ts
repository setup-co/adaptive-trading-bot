import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { dashboardApi, DashboardApiError } from "./client";
import {
  chartFixture,
  decisionsPageFixture,
  snapshotFixture,
  tradesPageFixture,
} from "../test/fixtures";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("read-only API client", () => {
  it("uses GET and preserves Decimal strings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(snapshotFixture));
    vi.stubGlobal("fetch", fetchMock);

    const snapshot = await dashboardApi.snapshot();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/dashboard/snapshot",
      expect.objectContaining({ method: "GET" }),
    );
    expect(snapshot.account.equity).toBe("10019.010100");
    expect(typeof snapshot.account.equity).toBe("string");
    expect(snapshot.markets[0].midpoint).toBe("12345678901234567890.1234567890124");
  });

  it("fails closed on malformed success JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ observed_at: "not-utc" })));

    await expect(dashboardApi.snapshot()).rejects.toMatchObject({
      kind: "malformed",
      code: "MALFORMED_RESPONSE",
    });
  });

  it.each([
    ["chart", chartFixture],
    ["decisions", decisionsPageFixture],
    ["trades", tradesPageFixture],
  ] as const)("runtime-validates the %s transport", async (endpoint, fixture) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(fixture)));

    const result = await dashboardApi[endpoint]({});

    expect(result).toEqual(fixture);
  });

  it.each([
    [400, "invalid_request", "DASHBOARD_CHART_INVALID_QUERY", "invalid-query"],
    [503, "unavailable", "DASHBOARD_PROVIDER_UNAVAILABLE", "unavailable"],
  ])("sanitizes structured %s responses", async (status, state, code, kind) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({ status: state, code, message: "Authoritative observation unavailable" }, status),
      ),
    );

    await expect(dashboardApi.snapshot()).rejects.toMatchObject({ kind, code, status });
  });

  it("does not expose raw network errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("api_key=do-not-render")));

    await expect(dashboardApi.health()).rejects.toEqual(
      expect.objectContaining<Partial<DashboardApiError>>({
        message: "The observation service could not be reached.",
      }),
    );
  });

  it("contains no mutation method or mutation endpoint client", () => {
    expect(Object.keys(dashboardApi).sort()).toEqual([
      "chart",
      "decisions",
      "health",
      "snapshot",
      "trades",
    ]);
    const source = readFileSync(fileURLToPath(import.meta.url).replace(/client\.test\.ts$/, "client.ts"), "utf8");
    expect(source).not.toMatch(/method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/);
    expect(source).not.toMatch(/\/orders|\/trade\b|\/settings/);
  });
});
