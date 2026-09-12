import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const chartLibrary = vi.hoisted(() => {
  const candleSeries = { setData: vi.fn() };
  const volumeSeries = {
    setData: vi.fn(),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
  };
  const chart = {
    addSeries: vi.fn((series: string) =>
      series === "CandlestickSeries" ? candleSeries : volumeSeries,
    ),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  };
  return {
    chart,
    createChart: vi.fn(() => chart),
    createSeriesMarkers: vi.fn(),
  };
});

vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries",
  HistogramSeries: "HistogramSeries",
  ColorType: { Solid: "Solid" },
  createChart: chartLibrary.createChart,
  createSeriesMarkers: chartLibrary.createSeriesMarkers,
}));

import { chartFixture } from "../test/fixtures";
import { CandleChart, mapMarkersToCandleDisplayTimes } from "./CandleChart";

const firstCandleOpen = Math.floor(Date.parse(chartFixture.candles[0].open_time) / 1000);
const secondCandleOpen = Math.floor(Date.parse(chartFixture.candles[1].open_time) / 1000);

describe("CandleChart marker display mapping", () => {
  beforeEach(() => vi.clearAllMocks());

  it("maps intra-candle entry and exit timestamps to the containing candle open and renders them", async () => {
    const data = {
      ...chartFixture,
      markers: [
        { ...chartFixture.markers[0], timestamp: "2026-09-10T11:37:00Z" },
        {
          ...chartFixture.markers[0],
          kind: "COMPLETED_TRADE_EXIT",
          identity: "fill-exit",
          side: "SELL",
          label: "completed trade exit",
          timestamp: "2026-09-10T11:44:59Z",
        },
      ],
    };
    const mapping = mapMarkersToCandleDisplayTimes(data);

    expect(mapping.rendered.map((marker) => marker.time)).toEqual([
      firstCandleOpen,
      firstCandleOpen,
    ]);
    expect(mapping.notRendered).toEqual([]);

    render(<CandleChart data={data} />);
    await waitFor(() => expect(chartLibrary.createSeriesMarkers).toHaveBeenCalledOnce());
    expect(chartLibrary.createSeriesMarkers.mock.calls[0][1]).toEqual(mapping.rendered);
  });

  it("keeps an exact-on-open marker at that candle data-point time", () => {
    const mapping = mapMarkersToCandleDisplayTimes({
      ...chartFixture,
      markers: [{ ...chartFixture.markers[0], timestamp: "2026-09-10T12:00:00Z" }],
    });

    expect(mapping.rendered).toHaveLength(1);
    expect(mapping.rendered[0].time).toBe(secondCandleOpen);
    expect(mapping.notRendered).toEqual([]);
  });

  it("does not snap a gap marker and explicitly reports that it is not rendered", () => {
    const gapMarker = {
      ...chartFixture.markers[0],
      timestamp: "2026-09-10T11:50:00Z",
      price: "102.123456789012345678901",
    };
    const data = { ...chartFixture, markers: [gapMarker] };
    const mapping = mapMarkersToCandleDisplayTimes(data);

    expect(mapping.rendered).toEqual([]);
    expect(mapping.notRendered).toEqual([gapMarker]);

    render(<CandleChart data={{ ...data, candles: [] }} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Not rendered on chart — no returned candle contains these marker times",
    );
    expect(screen.getByRole("status")).toHaveTextContent("2026-09-10T11:50:00Z");
    expect(screen.getByRole("status")).toHaveTextContent("102.123456789012345678901");
  });

  it("preserves the authoritative timestamp and Decimal price outside display mapping", () => {
    const data = {
      ...chartFixture,
      markers: [
        {
          ...chartFixture.markers[0],
          timestamp: "2026-09-10T11:37:12.345678Z",
          price: "100.05000000000000000001",
        },
      ],
    };
    const before = structuredClone(data);

    mapMarkersToCandleDisplayTimes(data);

    expect(data).toEqual(before);
    expect(data.markers[0].timestamp).toBe("2026-09-10T11:37:12.345678Z");
    expect(data.markers[0].price).toBe("100.05000000000000000001");
  });
});
