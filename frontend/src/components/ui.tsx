import clsx from "clsx";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Inbox,
  Loader2,
  Minus,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { pct, signedMoney, tone } from "../lib/format";
import { Sparkline } from "./Sparkline";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={clsx("card", className)}>{children}</section>;
}

export function CardHeader({
  title,
  icon: Icon,
  subtitle,
  actions,
}: {
  title: string;
  icon?: LucideIcon;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line/60 px-4 py-3">
      <div className="flex min-w-0 items-center gap-2.5">
        {Icon && (
          <span className="grid h-7 w-7 place-items-center rounded-md bg-raised text-muted">
            <Icon size={15} strokeWidth={1.8} />
          </span>
        )}
        <div className="min-w-0">
          <h2 className="truncate text-[13px] font-semibold">{title}</h2>
          {subtitle && <p className="truncate text-[12px] text-muted">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}

export function PageHeader({
  title,
  icon: Icon,
  description,
  actions,
}: {
  title: string;
  icon: LucideIcon;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl border border-line bg-surface text-gold">
          <Icon size={19} strokeWidth={1.8} />
        </span>
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">{title}</h1>
          {description && <p className="text-[13px] text-muted">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

type BadgeTone = "gain" | "loss" | "warn" | "accent" | "muted" | "gold";

const BADGE: Record<BadgeTone, string> = {
  gain: "bg-gain/10 text-gain ring-gain/25",
  loss: "bg-loss/10 text-loss ring-loss/25",
  warn: "bg-warn/10 text-warn ring-warn/25",
  accent: "bg-accent/10 text-accent ring-accent/25",
  gold: "bg-gold/10 text-gold ring-gold/25",
  muted: "bg-raised text-muted ring-line",
};

export function Badge({
  tone: t = "muted",
  icon: Icon,
  children,
  className,
}: {
  tone?: BadgeTone;
  icon?: LucideIcon;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        BADGE[t],
        className,
      )}
    >
      {Icon && <Icon size={12} strokeWidth={2} />}
      {children}
    </span>
  );
}

export function StatusDot({ on, pulse = false }: { on: boolean; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2 w-2">
      {on && pulse && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-gain/60" />}
      <span className={clsx("relative inline-flex h-2 w-2 rounded-full", on ? "bg-gain" : "bg-muted/60")} />
    </span>
  );
}

/** Kar/zarar: renk + ok ikonu + isaret (renk korlugune dayanikli). */
export function Pnl({
  value,
  kind = "pct",
  className,
  size = "sm",
}: {
  value: number | null | undefined;
  kind?: "pct" | "money";
  className?: string;
  size?: "sm" | "lg";
}) {
  const t = tone(value);
  const Icon = t === "gain" ? ArrowUpRight : t === "loss" ? ArrowDownRight : Minus;
  return (
    <span
      className={clsx(
        "num inline-flex items-center gap-0.5 font-medium",
        t === "gain" && "text-gain",
        t === "loss" && "text-loss",
        t === "flat" && "text-muted",
        size === "lg" && "text-[15px]",
        className,
      )}
    >
      <Icon size={size === "lg" ? 16 : 13} strokeWidth={2.2} aria-hidden />
      {kind === "pct" ? pct(value) : signedMoney(value)}
    </span>
  );
}

export function KpiCard({
  label,
  value,
  icon: Icon,
  delta,
  deltaKind = "pct",
  hint,
  spark,
}: {
  label: string;
  value: ReactNode;
  icon: LucideIcon;
  delta?: number | null;
  deltaKind?: "pct" | "money";
  hint?: ReactNode;
  spark?: (number | null)[];
}) {
  return (
    <Card className="relative overflow-hidden p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="label">{label}</div>
          <div className="num mt-1.5 truncate text-[22px] font-semibold leading-tight">{value}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[12px] text-muted">
            {delta !== undefined && <Pnl value={delta} kind={deltaKind} />}
            {hint}
          </div>
        </div>
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-raised text-gold">
          <Icon size={17} strokeWidth={1.8} />
        </span>
      </div>
      {spark && spark.length > 1 && (
        <div className="mt-3 h-10">
          <Sparkline values={spark} />
        </div>
      )}
    </Card>
  );
}

export function Spinner({ label = "Yükleniyor" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-muted" role="status">
      <Loader2 size={16} className="animate-spin" />
      {label}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-md bg-raised", className)} />;
}

export function EmptyState({ icon: Icon = Inbox, title, children }: { icon?: LucideIcon; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-full bg-raised text-muted">
        <Icon size={20} strokeWidth={1.6} />
      </span>
      <div className="text-[13px] font-medium">{title}</div>
      {children && <div className="max-w-md text-[12px] text-muted">{children}</div>}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="m-4 flex items-start gap-2 rounded-lg border border-loss/30 bg-loss/10 px-3 py-2.5 text-[13px] text-loss">
      <AlertTriangle size={16} className="mt-0.5 shrink-0" />
      <span>{msg}</span>
    </div>
  );
}

export function Notice({ tone: t = "warn", icon: Icon = AlertTriangle, children }: {
  tone?: "warn" | "loss" | "accent";
  icon?: LucideIcon;
  children: ReactNode;
}) {
  return (
    <div
      className={clsx(
        "flex items-start gap-2 rounded-lg border px-3 py-2.5 text-[12.5px]",
        t === "warn" && "border-warn/30 bg-warn/10 text-warn",
        t === "loss" && "border-loss/30 bg-loss/10 text-loss",
        t === "accent" && "border-accent/30 bg-accent/10 text-accent",
      )}
    >
      <Icon size={15} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

export function Pagination({
  page,
  size,
  total,
  onPage,
}: {
  page: number;
  size: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 text-[12px] text-muted">
      <span className="num">
        {total === 0 ? 0 : (page - 1) * size + 1}–{Math.min(page * size, total)} / {total}
      </span>
      <div className="flex items-center gap-1">
        <button className="btn-ghost px-2 py-1" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Önceki">
          <ChevronLeft size={15} />
        </button>
        <span className="num px-2">
          {page} / {pages}
        </span>
        <button className="btn-ghost px-2 py-1" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Sonraki">
          <ChevronRight size={15} />
        </button>
      </div>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-bg/60 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition",
            value === o.value ? "bg-raised text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label mb-1.5 block">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-muted">{hint}</span>}
    </label>
  );
}

export function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-[13px]">
      <span className="text-muted">{label}</span>
      <span className="num text-right">{children}</span>
    </div>
  );
}
