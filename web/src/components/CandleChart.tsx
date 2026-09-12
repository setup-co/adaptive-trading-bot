import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type Time,
} from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";

import type { DashboardChart } from "../api/schemas";

type ChartMarker = DashboardChart["markers"][number];

type MarkerDisplayMapping = {
  rendered: Array<{
    time: Time;
    position: "aboveBar" | "belowBar";
    color: string;
    shape: "arrowDown" | "arrowUp";
    text: string;
  }>;
  notRendered: ChartMarker[];
};

// Exported only for deterministic adapter-boundary regression tests.
// eslint-disable-next-line react-refresh/only-export-components
export function mapMarkersToCandleDisplayTimes(data: DashboardChart): MarkerDisplayMapping {
  const rendered: MarkerDisplayMapping["rendered"] = [];
  const notRendered: ChartMarker[] = [];

  for (const marker of data.markers) {
    const markerTime = Date.parse(marker.timestamp);
    const containingCandle = data.candles.find(
      (candle) =>
        Date.parse(candle.open_time) <= markerTime && markerTime < Date.parse(candle.close_time),
    );
    if (containingCandle === undefined) {
      notRendered.push(marker);
      continue;
    }
    rendered.push({
      // This candle open is a display coordinate only. The marker timestamp remains unchanged.
      time: Math.floor(Date.parse(containingCandle.open_time) / 1000) as Time,
      position: marker.side === "BUY" ? "belowBar" : "aboveBar",
      color: marker.side === "BUY" ? "#3dd6a6" : "#ff9d66",
      shape: marker.side === "BUY" ? "arrowUp" : "arrowDown",
      text: marker.label,
    });
  }
  return { rendered, notRendered };
}

export function CandleChart({ data }: { data: DashboardChart }) {
  const target = useRef<HTMLDivElement>(null);
  const markerMapping = useMemo(() => mapMarkersToCandleDisplayTimes(data), [data]);

  useEffect(() => {
    if (!target.current || data.candles.length === 0) return;
    const chart = createChart(target.current, {
      height: 420,
      layout: { background: { type: ColorType.Solid, color: "#0b1728" }, textColor: "#a9b8cc" },
      grid: { vertLines: { color: "#17283d" }, horzLines: { color: "#17283d" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#29405c" },
      rightPriceScale: { borderColor: "#29405c" },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#3dd6a6",
      downColor: "#ff6b7a",
      wickUpColor: "#3dd6a6",
      wickDownColor: "#ff6b7a",
      borderVisible: false,
    });
    // Numeric conversion is intentionally confined to this rendering adapter.
    candles.setData(
      data.candles.map((item) => ({
        time: Math.floor(Date.parse(item.open_time) / 1000) as Time,
        open: Number(item.open),
        high: Number(item.high),
        low: Number(item.low),
        close: Number(item.close),
      })),
    );
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      color: "#4c83bf88",
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(
      data.candles.map((item) => ({
        time: Math.floor(Date.parse(item.open_time) / 1000) as Time,
        value: Number(item.volume),
      })),
    );
    createSeriesMarkers(candles, markerMapping.rendered);
    chart.timeScale().fitContent();
    const resize = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: entry.contentRect.width });
    });
    resize.observe(target.current);
    return () => {
      resize.disconnect();
      chart.remove();
    };
  }, [data, markerMapping]);

  return (
    <>
      {data.candles.length === 0 ? (
        <div className="chart-empty">No candles in range.</div>
      ) : (
        <div className="chart-canvas" data-testid="candle-chart" ref={target} />
      )}
      {markerMapping.notRendered.length > 0 && (
        <section className="marker-render-warning" role="status">
          <strong>Not rendered on chart — no returned candle contains these marker times</strong>
          <ul>
            {markerMapping.notRendered.map((marker) => (
              <li key={`${marker.identity}-${marker.kind}-${marker.timestamp}`}>
                {marker.label}: <time>{marker.timestamp}</time> @ <span className="mono">{marker.price}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
