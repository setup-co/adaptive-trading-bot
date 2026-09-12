import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { dashboardApi, DashboardApiError } from "../api/client";
import {
  chartFixture,
  decisionFixture,
  decisionsPageFixture,
  snapshotFixture,
  tradeFixture,
  tradesPageFixture,
  unavailable,
} from "../test/fixtures";
import { ChartPage } from "./ChartPage";
import { DecisionsPage } from "./DecisionsPage";
import { HealthPage } from "./HealthPage";
import { OverviewPage } from "./OverviewPage";
import { TradesPage } from "./TradesPage";

vi.mock("../components/CandleChart", () => ({
  CandleChart: ({ data }: { data: typeof chartFixture }) => (
    <div data-testid="chart-adapter">
      {data.symbol}:{data.candles.map((candle) => candle.open).join("|")}
    </div>
  ),
}));

describe("Overview", () => {
  it("renders authoritative available and unavailable facts without substitution", async () => {
    vi.spyOn(dashboardApi, "snapshot").mockResolvedValue(snapshotFixture);
    render(<OverviewPage />);

    expect(await screen.findByText("12345678901234567890.1234567890124")).toBeInTheDocument();
    expect(screen.getByText("UPTREND")).toBeInTheDocument();
    expect(screen.getByText("fundamental phase is unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
    expect(screen.getByText("LIVE DISABLED")).toBeInTheDocument();
    expect(screen.getAllByText("19.010100")).toHaveLength(2);
  });

  it("shows a truthful snapshot 503 state", async () => {
    vi.spyOn(dashboardApi, "snapshot").mockRejectedValue(
      new DashboardApiError(
        "unavailable",
        "dashboard snapshot is temporarily unavailable",
        "DASHBOARD_PROVIDER_FAILURE",
        503,
      ),
    );
    render(<OverviewPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "dashboard snapshot is temporarily unavailable",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("traceback");
  });
});

describe("Chart", () => {
  it("passes exact candles in order and shows integrity, markers, and unavailable overlays", async () => {
    vi.spyOn(dashboardApi, "chart").mockResolvedValue(chartFixture);
    render(<ChartPage />);

    expect(await screen.findByTestId("chart-adapter")).toHaveTextContent(
      "BTC/USDT:100.01|104.50",
    );
    expect(screen.getByText("DIRTY")).toBeInTheDocument();
    expect(screen.getByText(/no repair was applied/i)).toBeInTheDocument();
    expect(screen.getByText("completed trade entry")).toBeInTheDocument();
    expect(screen.getByText("100.05")).toBeInTheDocument();
    expect(screen.getByText("2026-09-10T11:30:00Z · BUY")).toBeInTheDocument();
    expect(screen.getAllByText("authoritative historical feature series is unavailable")).toHaveLength(4);
    expect(screen.queryByText(/calculated/i)).not.toBeInTheDocument();
  });

  it("shows an explicit empty state without hiding returned integrity", async () => {
    vi.spyOn(dashboardApi, "chart").mockResolvedValue({
      ...chartFixture,
      candles: [],
      integrity: { ...chartFixture.integrity, rows_scanned: 0 },
    });
    render(<ChartPage />);

    expect(await screen.findByText("No authoritative candles matched this range.")).toBeInTheDocument();
    expect(screen.getByText("Chart integrity")).toBeInTheDocument();
  });

  it("renders a sanitized invalid-query state", async () => {
    vi.spyOn(dashboardApi, "chart").mockRejectedValue(
      new DashboardApiError(
        "invalid-query",
        "dashboard chart query is invalid",
        "DASHBOARD_CHART_INVALID_QUERY",
        400,
      ),
    );
    render(<ChartPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid query");
    expect(screen.getByRole("alert")).toHaveTextContent("dashboard chart query is invalid");
  });

  it("aborts an older chart request when query controls change", async () => {
    const pending: Array<{
      signal: AbortSignal | undefined;
      resolve: (value: typeof chartFixture) => void;
    }> = [];
    vi.spyOn(dashboardApi, "chart").mockImplementation(
      (_query, signal) =>
        new Promise((resolve) => {
          pending.push({ signal, resolve });
        }),
    );
    const user = userEvent.setup();
    render(<ChartPage />);
    await waitFor(() => expect(pending).toHaveLength(1));

    await user.selectOptions(screen.getByLabelText("Symbol"), "ETH/USDT");
    await user.click(screen.getByRole("button", { name: "Load range" }));
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(pending[0].signal?.aborted).toBe(true);
    pending[1].resolve({ ...chartFixture, symbol: "ETH/USDT" });
    expect(await screen.findByTestId("chart-adapter")).toHaveTextContent(
      "ETH/USDT:100.01|104.50",
    );
    pending[0].resolve(chartFixture);
    expect(screen.getByTestId("chart-adapter")).toHaveTextContent("ETH/USDT");
  });
});

describe("Decisions", () => {
  it("keeps decision categories and Strategy/Risk/Safety evidence separate", async () => {
    const records = [
      decisionFixture,
      { ...decisionFixture, identity: { ...decisionFixture.identity, strategy_id: "safe" }, safety: { ...decisionFixture.safety, status: "SAFE_MODE" } },
      { ...decisionFixture, identity: { ...decisionFixture.identity, strategy_id: "clear" }, strategy: { ...decisionFixture.strategy, action: "LONG_CANDIDATE" }, risk: { ...decisionFixture.risk, status: "APPROVED" }, safety: { ...decisionFixture.safety, status: "CLEARED" } },
    ];
    vi.spyOn(dashboardApi, "decisions").mockResolvedValue({
      ...decisionsPageFixture,
      total_matched: records.length,
      returned_count: records.length,
      records,
    });
    render(<DecisionsPage />);

    expect(await screen.findAllByText("NO TRADE")).not.toHaveLength(0);
    expect(screen.getAllByText("REJECTED")).not.toHaveLength(0);
    expect(screen.getAllByText("BLOCKED BY RISK")).not.toHaveLength(0);
    expect(screen.getAllByText("SAFE MODE")).not.toHaveLength(0);
    expect(screen.getAllByText("CLEARED")).not.toHaveLength(0);
    expect(screen.getAllByRole("heading", { name: "Strategy" })).toHaveLength(3);
    expect(screen.getAllByRole("heading", { name: "Risk" })).toHaveLength(3);
    expect(screen.getAllByRole("heading", { name: "Operational Safety" })).toHaveLength(3);
    expect(screen.getAllByText("strategy rule audit is not safely serializable")).toHaveLength(3);
  });

  it("renders an explicit empty valid result", async () => {
    vi.spyOn(dashboardApi, "decisions").mockResolvedValue({
      ...decisionsPageFixture,
      total_matched: 0,
      returned_count: 0,
      records: [],
    });
    render(<DecisionsPage />);

    expect(await screen.findByText("No authoritative records matched this query.")).toBeInTheDocument();
  });

  it("shows a completed outcome only when marked available", async () => {
    vi.spyOn(dashboardApi, "decisions").mockResolvedValue({
      ...decisionsPageFixture,
      total_matched: 2,
      returned_count: 2,
      records: [
        {
          ...decisionFixture,
          identity: { ...decisionFixture.identity, strategy_id: "unlinked" },
        },
        {
          ...decisionFixture,
          completed_trade: { availability: "AVAILABLE", value: tradeFixture, reason: null },
        },
      ],
    });
    render(<DecisionsPage />);

    expect(await screen.findByText(/linked by exact decision identity/i)).toBeInTheDocument();
    expect(screen.getByText("no completed trade exists for this exact decision identity")).toBeInTheDocument();
  });
});

describe("Trades", () => {
  it("renders exact outcome strings and explicit deferred analytics", async () => {
    vi.spyOn(dashboardApi, "trades").mockResolvedValue(tradesPageFixture);
    render(<TradesPage />);

    expect(await screen.findByText("-1.30864196308024690290")).toBeInTheDocument();
    expect(screen.getByText("0.009358024643024691236")).toBeInTheDocument();
    expect(screen.getByText("-1.317999987723271594136")).toBeInTheDocument();
    expect(screen.getByText("-1.067580000000000000")).toBeInTheDocument();
    expect(screen.getAllByText("authoritative completed-trade analytics are unavailable")).toHaveLength(3);
  });
});

describe("Health", () => {
  it("renders liveness and per-source safety without inventing an aggregate", async () => {
    vi.spyOn(dashboardApi, "health").mockResolvedValue({ status: "ok", service: "adaptive-trading-bot" });
    vi.spyOn(dashboardApi, "snapshot").mockResolvedValue({
      ...snapshotFixture,
      system: { ...snapshotFixture.system, global_safety_status: unavailable("no authoritative aggregate exists") },
    });
    render(<HealthPage />);

    expect(await screen.findByText("adaptive-trading-bot")).toBeInTheDocument();
    expect(screen.getByText("Liveness only — not trading readiness.")).toBeInTheDocument();
    expect(screen.getByText("CLEARED")).toBeInTheDocument();
    expect(screen.getByText("no authoritative aggregate exists")).toBeInTheDocument();
    expect(screen.getByText(/does not infer a global safety state/i)).toBeInTheDocument();
  });
});
