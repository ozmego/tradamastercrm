import {
  ColorType,
  CrosshairMode,
  LineStyle,
  TickMarkType,
  createChart,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Candle } from "../api/types";
import { tokenColor, type Theme } from "../lib/theme";

function baseOptions(height: number) {
  return {
    height,
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: tokenColor("muted"),
      fontFamily: "JetBrains Mono, ui-monospace, monospace",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: tokenColor("line", 0.35) },
      horzLines: { color: tokenColor("line", 0.35) },
    },
    rightPriceScale: { borderColor: tokenColor("line", 0.8) },
    timeScale: {
      borderColor: tokenColor("line", 0.8),
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time: Time, type: TickMarkType) => {
        const d = new Date(Number(time) * 1000);
        if (type === TickMarkType.Year) return d.toLocaleDateString("tr-TR", { year: "numeric", timeZone: "UTC" });
        if (type === TickMarkType.Month) return d.toLocaleDateString("tr-TR", { month: "short", timeZone: "UTC" });
        if (type === TickMarkType.DayOfMonth) return d.toLocaleDateString("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
        return d.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
      },
    },
    crosshair: { mode: CrosshairMode.Normal },
    localization: {
      locale: "tr-TR",
      priceFormatter: (p: number) => p.toLocaleString("tr-TR", { maximumFractionDigits: p >= 1000 ? 0 : p >= 1 ? 2 : 6 }),
    },
    handleScale: { axisPressedMouseMove: false },
  } as const;
}

function useChart(height: number, theme: Theme, build: (chart: IChartApi) => void, deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, baseOptions(height));
    build(chart);
    chart.timeScale().fitContent();
    return () => chart.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height, theme, ...deps]);
  return ref;
}

const toTs = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

/** Equity / portfoy degeri alan grafigi. */
export function EquityChart({
  points,
  theme,
  height = 260,
  baseline,
}: {
  points: { t: string; v: number | null }[];
  theme: Theme;
  height?: number;
  baseline?: number | null;
}) {
  const data = points
    .filter((p): p is { t: string; v: number } => typeof p.v === "number")
    .map((p) => ({ time: toTs(p.t), value: p.v }))
    // ayni saniyeye dusen noktalar: grafik artan zaman ister
    .filter((p, i, arr) => i === 0 || p.time > arr[i - 1]!.time);
  const ref = useChart(
    height,
    theme,
    (chart) => {
      const first = data[0]?.value ?? 0;
      const last = data[data.length - 1]?.value ?? 0;
      const up = last >= (baseline ?? first);
      const color = up ? "gain" : "loss";
      const series = chart.addAreaSeries({
        lineColor: tokenColor(color),
        topColor: tokenColor(color, 0.28),
        bottomColor: tokenColor(color, 0.0),
        lineWidth: 2,
        priceLineVisible: false,
      });
      series.setData(data);
      if (baseline) {
        series.createPriceLine({
          price: baseline,
          color: tokenColor("muted", 0.8),
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: "başlangıç",
        });
      }
    },
    [JSON.stringify(data), baseline],
  );
  return <div ref={ref} style={{ height }} className="w-full" />;
}

/** Mum grafigi: giris/cikis isaretleri + stop / kar al / ilk stop cizgileri. */
export function CandleChart({
  candles,
  theme,
  entry,
  exit,
  levels,
  height = 380,
}: {
  candles: Candle[];
  theme: Theme;
  entry?: { time: number; price: number } | null;
  exit?: { time: number; price: number } | null;
  levels?: { stop: number | null; take_profit: number | null; initial_stop: number | null };
  height?: number;
}) {
  const ref = useChart(
    height,
    theme,
    (chart) => {
      const series = chart.addCandlestickSeries({
        upColor: tokenColor("gain"),
        downColor: tokenColor("loss"),
        wickUpColor: tokenColor("gain"),
        wickDownColor: tokenColor("loss"),
        borderVisible: false,
      });
      series.setData(candles.map((c) => ({ ...c, time: c.time as UTCTimestamp })));

      const line = (price: number | null | undefined, color: string, title: string, style = LineStyle.Dashed) => {
        if (typeof price === "number") {
          series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
        }
      };
      if (entry) line(entry.price, tokenColor("accent"), "giriş", LineStyle.Solid);
      line(levels?.stop, tokenColor("loss"), "stop");
      line(levels?.take_profit, tokenColor("gain"), "kâr al");
      if (levels?.initial_stop && levels.initial_stop !== levels.stop) {
        line(levels.initial_stop, tokenColor("loss", 0.45), "ilk stop", LineStyle.Dotted);
      }

      // isaretler mum zamanina hizalanmali
      const snap = (t: number) => {
        let best = candles[0]?.time ?? t;
        for (const c of candles) if (c.time <= t) best = c.time;
        return best as Time;
      };
      const markers: SeriesMarker<Time>[] = [];
      if (entry && candles.length) {
        markers.push({ time: snap(entry.time), position: "belowBar", color: tokenColor("accent"), shape: "arrowUp", text: "AL" });
      }
      if (exit && candles.length) {
        markers.push({ time: snap(exit.time), position: "aboveBar", color: tokenColor("gold"), shape: "arrowDown", text: "SAT" });
      }
      series.setMarkers(markers.sort((a, b) => Number(a.time) - Number(b.time)));
    },
    [candles, entry?.time, exit?.time, levels?.stop, levels?.take_profit],
  );
  return <div ref={ref} style={{ height }} className="w-full" />;
}

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export interface PnlPoint {
  t: string;
  cum: number;
  pnl: number;
  symbol: string | null;
}

/** Kumulatif gerceklesen K/Z: 0 cizgisinin ustu kar, alti zarar rengi; uzerine gelince islem ayrintisi. */
export function PnlChart({ points, theme, height = 240 }: { points: PnlPoint[]; theme: Theme; height?: number }) {
  // Ayni saniyeye dusen kapanislar: grafik artan zaman ister -> o saniyenin son kumulatif degeri.
  const byTime = new Map<number, { value: number; trades: PnlPoint[] }>();
  for (const p of points) {
    const time = toTs(p.t);
    const prev = byTime.get(time);
    byTime.set(time, { value: p.cum, trades: [...(prev?.trades ?? []), ...(p.symbol ? [p] : [])] });
  }
  const data = [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([time, v]) => ({ time: time as UTCTimestamp, value: v.value }));
  const tipRef = useRef<HTMLDivElement>(null);

  const ref = useChart(
    height,
    theme,
    (chart) => {
      const series = chart.addBaselineSeries({
        baseValue: { type: "price", price: 0 },
        topLineColor: tokenColor("gain"),
        topFillColor1: tokenColor("gain", 0.22),
        topFillColor2: tokenColor("gain", 0.02),
        bottomLineColor: tokenColor("loss"),
        bottomFillColor1: tokenColor("loss", 0.02),
        bottomFillColor2: tokenColor("loss", 0.22),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        lineType: 1, // basamak: K/Z yalnizca islem kapaninca degisir
      });
      series.setData(data);
      series.createPriceLine({ price: 0, color: tokenColor("muted", 0.7), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
      chart.subscribeCrosshairMove((param) => {
        const tip = tipRef.current;
        if (!tip) return;
        const pt = param.time !== undefined ? byTime.get(Number(param.time)) : undefined;
        if (!pt || !param.point) {
          tip.style.display = "none";
          return;
        }
        const d = new Date(Number(param.time) * 1000);
        const fmt = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const trades = pt.trades
          .map((tr) => `<div class="flex justify-between gap-4"><span>${escapeHtml(tr.symbol ?? "")}</span><span class="num">${fmt(tr.pnl)}</span></div>`)
          .join("");
        tip.innerHTML =
          `<div class="text-muted">${d.toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</div>` +
          `<div class="num mt-0.5 text-[13px] font-semibold">${fmt(pt.value)} USDT</div>` +
          (trades ? `<div class="mt-1 border-t border-line/60 pt-1 text-muted">${trades}</div>` : "");
        tip.style.display = "block";
        const w = tip.offsetWidth;
        const x = param.point.x + 14 + w > (ref.current?.clientWidth ?? 0) ? param.point.x - w - 14 : param.point.x + 14;
        tip.style.transform = `translate(${x}px, ${Math.max(4, param.point.y - 30)}px)`;
      });
    },
    [JSON.stringify(points)],
  );
  return (
    <div className="relative w-full">
      <div ref={ref} style={{ height }} className="w-full" />
      <div
        ref={tipRef}
        className="pointer-events-none absolute left-0 top-0 z-10 hidden min-w-[150px] rounded-lg border border-line bg-surface/95 px-3 py-2 text-[11.5px] shadow-lg"
      />
    </div>
  );
}
