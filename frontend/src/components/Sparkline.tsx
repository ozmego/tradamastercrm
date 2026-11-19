import { useId } from "react";

/** Hafif SVG sparkline: son deger ilk degerden yuksekse yesil, dusukse kirmizi. */
export function Sparkline({ values, height = 40 }: { values: (number | null)[]; height?: number }) {
  const id = useId();
  const pts = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (pts.length < 2) return null;
  const w = 200;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const xy = pts.map((v, i) => [(i / (pts.length - 1)) * w, height - 3 - ((v - min) / span) * (height - 6)] as const);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${w},${height} L0,${height} Z`;
  const up = pts[pts.length - 1]! >= pts[0]!;
  const color = up ? "rgb(var(--gain))" : "rgb(var(--loss))";
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="h-full w-full" aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
