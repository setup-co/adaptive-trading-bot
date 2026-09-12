import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { DashboardChart } from "../api/schemas";

export function CandleChart({ data }: { data: DashboardChart }) {
  const target = useRef<HTMLDivElement>(null);

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
    createSeriesMarkers(
      candles,
      data.markers.map((marker) => ({
        time: Math.floor(Date.parse(marker.timestamp) / 1000) as Time,
        position: marker.side === "BUY" ? "belowBar" : "aboveBar",
        color: marker.side === "BUY" ? "#3dd6a6" : "#ff9d66",
        shape: marker.side === "BUY" ? "arrowUp" : "arrowDown",
        text: marker.label,
      })),
    );
    chart.timeScale().fitContent();
    const resize = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: entry.contentRect.width });
    });
    resize.observe(target.current);
    return () => {
      resize.disconnect();
      chart.remove();
    };
  }, [data]);

  if (data.candles.length === 0) return <div className="chart-empty">No candles in range.</div>;
  return <div className="chart-canvas" data-testid="candle-chart" ref={target} />;
}
