import { Activity, Ban, CircleCheck, CircleSlash, Clock, Gauge, Power, ShieldOff, TriangleAlert } from "lucide-react";

import type { Account, Drift } from "../api/types";
import { pct } from "../lib/format";
import { Badge } from "./ui";

export function ModeBadge({ mode }: { mode: "paper" | "live" }) {
  return mode === "live" ? (
    <Badge tone="gold" icon={Power}>
      CANLI
    </Badge>
  ) : (
    <Badge tone="accent">PAPER</Badge>
  );
}

export function BuyPermissionBadge({ account }: { account: Account }) {
  const f = account.flags;
  if (f.kill_switch)
    return (
      <Badge tone="loss" icon={ShieldOff}>
        Kill-switch
      </Badge>
    );
  if (account.unresolved_orders > 0)
    return (
      <Badge tone="loss" icon={TriangleAlert}>
        Belirsiz emir
      </Badge>
    );
  return f.buy_allowed ? (
    <Badge tone="gain" icon={CircleCheck}>
      Alım açık
    </Badge>
  ) : (
    <Badge tone="muted" icon={CircleSlash}>
      Alım kapalı
    </Badge>
  );
}

export function DriftBadge({ drift }: { drift: Drift | null }) {
  if (!drift)
    return (
      <Badge tone="muted" icon={Gauge}>
        İzlenmiyor
      </Badge>
    );
  switch (drift.status) {
    case "degraded":
      return (
        <Badge tone="loss" icon={Activity}>
          Bozulma
        </Badge>
      );
    case "ok":
      return (
        <Badge tone="gain" icon={Activity}>
          Beklenen aralıkta
        </Badge>
      );
    case "insufficient":
      return (
        <Badge tone="muted" icon={Clock}>
          {drift.n} işlem · veri birikiyor
        </Badge>
      );
    default:
      return (
        <Badge tone="warn" icon={Ban}>
          Baseline yok
        </Badge>
      );
  }
}

export function DriftDetail({ drift }: { drift: Drift | null }) {
  if (!drift) return <p className="text-[12.5px] text-muted">Hesabın profil/zaman dilimi kaydı yok.</p>;
  return (
    <div className="space-y-1.5 text-[12.5px]">
      <div className="flex justify-between">
        <span className="text-muted">Son işlemler</span>
        <span className="num">{drift.n}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-muted">Canlı ort. getiri</span>
        <span className="num">{pct(drift.live_mean)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-muted">Alarm sınırı (%5)</span>
        <span className="num">{pct(drift.lower_bound)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-muted">Backtest ort.</span>
        <span className="num">{pct(drift.baseline_mean)}</span>
      </div>
    </div>
  );
}

export function SideBadge({ side }: { side: "BUY" | "SELL" }) {
  return side === "BUY" ? <Badge tone="gain">AL</Badge> : <Badge tone="loss">SAT</Badge>;
}

const ORDER_TONE: Record<string, "gain" | "loss" | "warn" | "muted" | "accent"> = {
  FILLED: "gain",
  REJECTED: "muted",
  UNKNOWN: "loss",
  ABANDONED: "warn",
  NEW: "accent",
};

const ORDER_LABEL: Record<string, string> = {
  FILLED: "Doldu",
  REJECTED: "Reddedildi",
  UNKNOWN: "Belirsiz",
  ABANDONED: "Terk edildi",
  NEW: "Yeni",
};

export function OrderStatusBadge({ status }: { status: string }) {
  return <Badge tone={ORDER_TONE[status] ?? "muted"}>{ORDER_LABEL[status] ?? status}</Badge>;
}

/** Gunluk hedef ilerlemesi: 0'dan hedefe dolan cubuk + metin. Zarar gunlerinde cubuk bos, metin zarari gosterir. */
export function DailyTargetProgress({ total, target }: { total: number; target: number | null }) {
  if (target == null) return null;
  const ratio = Math.max(0, Math.min(1, total / target));
  const hit = total >= target;
  return (
    <div className="mt-1 flex items-center justify-end gap-1.5" title={`Günlük hedef +${pct(target, 0, false)}: bugün ${pct(total)}`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-raised" role="progressbar" aria-valuenow={Math.round(ratio * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Günlük hedef ilerlemesi">
        <div className={hit ? "h-full bg-gain" : "h-full bg-accent/70"} style={{ width: `${ratio * 100}%` }} />
      </div>
      <span className="num text-[10.5px] text-muted">{hit ? "hedef doldu" : `hedef ${pct(target, 0, false)}`}</span>
    </div>
  );
}
