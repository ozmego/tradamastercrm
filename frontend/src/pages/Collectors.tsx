import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  CalendarClock,
  CircleAlert,
  CircleCheck,
  CircleX,
  Clock3,
  Cpu,
  HardDrive,
  History,
  Hourglass,
  RadioTower,
  RefreshCw,
  Table2,
  Terminal,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { CollectorHealth, CollectorRun, CollectorStatus, Freshness, RunStatus, RunsPage } from "../api/types";
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KpiCard,
  Notice,
  PageHeader,
  Pagination,
  Segmented,
  Spinner,
  StatusDot,
} from "../components/ui";
import { DASH, date, dateTime, relative } from "../lib/format";

// ------------------------------------------------------------------ etiketler
const COLLECTOR_TITLE: Record<string, string> = {
  derivatives: "Türev piyasa metrikleri",
};

const METRIC_LABEL: Record<string, string> = {
  open_interest: "Open interest",
  open_interest_value: "OI değeri (USDT)",
  long_short_ratio: "Long/short oranı",
};

const HEALTH: Record<CollectorStatus, { label: string; tone: "gain" | "warn" | "loss" | "muted"; icon: LucideIcon }> = {
  healthy: { label: "Sağlıklı", tone: "gain", icon: CircleCheck },
  degraded: { label: "Sorunlu", tone: "warn", icon: CircleAlert },
  stopped: { label: "Süreç durdu", tone: "warn", icon: CircleAlert },
  failing: { label: "Başarısız", tone: "loss", icon: CircleX },
  never: { label: "Hiç çalışmadı", tone: "muted", icon: Hourglass },
};

const RUN: Record<RunStatus, { label: string; tone: "gain" | "warn" | "loss" | "accent" | "muted" }> = {
  OK: { label: "Başarılı", tone: "gain" },
  PARTIAL: { label: "Kısmi", tone: "warn" },
  FAILED: { label: "Başarısız", tone: "loss" },
  INTERRUPTED: { label: "Yarıda kaldı", tone: "loss" },
  RUNNING: { label: "Çalışıyor", tone: "accent" },
};

const TRIGGER: Record<string, string> = { schedule: "Zamanlanmış", manual: "Panel", cli: "CLI" };

function duration(ms: number | null | undefined): string {
  if (ms == null) return DASH;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toLocaleString("tr-TR", { maximumFractionDigits: 1 })} sn`;
}

function n(x: number): string {
  return x.toLocaleString("tr-TR");
}

export function HealthBadge({ status }: { status: CollectorStatus }) {
  const h = HEALTH[status];
  return (
    <Badge tone={h.tone} icon={h.icon}>
      {h.label}
    </Badge>
  );
}

function RunBadge({ status }: { status: RunStatus }) {
  return <Badge tone={RUN[status].tone}>{RUN[status].label}</Badge>;
}

/** Sorunlari yapisal alanlardan Turkce uretir (backend metni ASCII). */
function issues(h: CollectorHealth): string[] {
  const out: string[] = [];
  if (h.status === "never") return ["Toplayıcı henüz hiç çalışmadı. Süreçler sayfasından başlatın ya da şimdi çalıştırın."];
  if (h.consecutive_failures > 0) out.push(`Üst üste ${h.consecutive_failures} başarısız çalışma.`);
  if (!h.process.running)
    out.push(`Süreç çalışmıyor. ${h.exchange_history_days} günden uzun kapalı kalırsa borsadaki veri geri alınamaz.`);
  if (h.last_run?.status === "PARTIAL")
    out.push(`Son çalışmada hata veren semboller: ${Object.keys(h.last_run.errors).join(", ") || "?"}.`);
  if (h.data.stale_symbols.length) out.push(`Bayat veri: ${h.data.stale_symbols.join(", ")}.`);
  if (h.data.missing_symbols.length) out.push(`Hiç veri yok: ${h.data.missing_symbols.join(", ")}.`);
  return out;
}

function LogView({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; // en yeni satirlar gorunsun
  }, [lines]);
  return (
    <pre ref={ref} className="num max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg/80 p-3 text-[11.5px] leading-relaxed text-muted">
      {lines.length ? lines.join("\n") : "Log boş"}
    </pre>
  );
}

// ------------------------------------------------------------------ tazelik
function FreshCell({ f }: { f: Freshness | undefined }) {
  if (!f || f.status === "missing")
    return (
      <span className="flex items-center gap-1.5 text-loss">
        <CircleX size={13} /> veri yok
      </span>
    );
  const stale = f.status === "stale";
  return (
    <div className="space-y-0.5">
      <div className={clsx("flex items-center gap-1.5", stale ? "text-warn" : "text-fg")}>
        {stale ? <CircleAlert size={13} /> : <CircleCheck size={13} className="text-gain" />}
        <span className="num">{relative(f.last)}</span>
      </div>
      <div className="num text-[11px] text-muted">
        {n(f.rows)} satır
        {f.lag_periods ? ` · ${f.lag_periods} sa gecikme` : ""}
        {f.missing_periods ? <span className="text-warn"> · {f.missing_periods} kayıp</span> : ""}
      </div>
    </div>
  );
}

function FreshnessTable({ h }: { h: CollectorHealth }) {
  const rows = h.freshness ?? [];
  const symbols = Array.from(new Set(rows.map((r) => r.symbol)));
  const by = new Map(rows.map((r) => [`${r.symbol}|${r.metric}`, r]));
  if (!symbols.length) return <EmptyState icon={Table2} title="Sembol yok" />;
  return (
    <div className="overflow-x-auto">
      <table className="table-base">
        <thead>
          <tr>
            <th>Sembol</th>
            {h.metrics.map((m) => (
              <th key={m}>{METRIC_LABEL[m] ?? m}</th>
            ))}
            <th>Kapsam</th>
          </tr>
        </thead>
        <tbody>
          {symbols.map((sym) => {
            const cells = h.metrics.map((m) => by.get(`${sym}|${m}`));
            const firsts = cells.map((c) => c?.first).filter(Boolean) as string[];
            const lasts = cells.map((c) => c?.last).filter(Boolean) as string[];
            const configured = cells.some((c) => c?.configured);
            const from = firsts.sort()[firsts.length - 1];
            const to = lasts.sort()[0];
            const days = from && to ? Math.round((new Date(to).getTime() - new Date(from).getTime()) / 86_400_000) : null;
            return (
              <tr key={sym}>
                <td>
                  <div className="font-medium">{sym}</div>
                  {!configured && <div className="text-[11px] text-muted">listede değil (siliniyor)</div>}
                </td>
                {cells.map((c, i) => (
                  <td key={h.metrics[i]}>
                    <FreshCell f={c} />
                  </td>
                ))}
                <td className="text-muted">
                  {days != null ? (
                    <>
                      <div className="num text-fg">{days} gün</div>
                      <div className="text-[11px]">
                        {date(from)} → {date(to)}
                      </div>
                    </>
                  ) : (
                    DASH
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ calismalar
type RunFilter = "all" | "OK" | "PARTIAL" | "FAILED";
const PAGE_SIZE = 15;

function RunsTable({ name }: { name: string }) {
  const [filter, setFilter] = useState<RunFilter>("all");
  const [page, setPage] = useState(1);
  const q = useQuery({
    queryKey: ["collector-runs", name, filter, page],
    queryFn: () =>
      api.get<RunsPage>(
        `/collectors/${name}/runs?limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}${filter === "all" ? "" : `&status=${filter}`}`,
      ),
    refetchInterval: 10_000,
  });
  return (
    <Card>
      <CardHeader
        title="Çalışma geçmişi"
        icon={History}
        subtitle="Her toplama: süre, eklenen ve silinen satır, hata veren semboller"
        actions={
          <Segmented<RunFilter>
            value={filter}
            onChange={(v) => {
              setFilter(v);
              setPage(1);
            }}
            options={[
              { value: "all", label: "Tümü" },
              { value: "OK", label: "Başarılı" },
              { value: "PARTIAL", label: "Kısmi" },
              { value: "FAILED", label: "Başarısız" },
            ]}
          />
        }
      />
      {q.error && <ErrorState error={q.error} />}
      {q.isLoading && <Spinner />}
      {q.data && q.data.items.length === 0 && (
        <EmptyState icon={History} title="Kayıt yok">
          Toplayıcı çalıştıkça burada görünür.
        </EmptyState>
      )}
      {q.data && q.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Başlangıç</th>
                  <th>Durum</th>
                  <th>Tetikleyen</th>
                  <th className="text-right">Süre</th>
                  <th className="text-right">Eklenen</th>
                  <th className="text-right">Silinen</th>
                  <th>Ayrıntı</th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((r: CollectorRun) => (
                  <tr key={r.id}>
                    <td>
                      <div className="num">{dateTime(r.started_at)}</div>
                      <div className="text-[11px] text-muted">{relative(r.started_at)}</div>
                    </td>
                    <td>
                      <RunBadge status={r.status} />
                    </td>
                    <td className="text-muted">{TRIGGER[r.trigger] ?? r.trigger}</td>
                    <td className="num text-right">{duration(r.duration_ms)}</td>
                    <td className={clsx("num text-right", r.rows_added > 0 && "text-gain")}>
                      {r.rows_added > 0 ? `+${n(r.rows_added)}` : "0"}
                    </td>
                    <td className="num text-right text-muted">{r.rows_purged > 0 ? `−${n(r.rows_purged)}` : "0"}</td>
                    <td className="max-w-[360px] text-[12px]">
                      {r.error && <div className="text-loss">{r.error}</div>}
                      {Object.entries(r.errors).map(([sym, err]) => (
                        <div key={sym} className="truncate text-warn" title={err}>
                          {sym}: {err}
                        </div>
                      ))}
                      {r.backfilled.length > 0 && (
                        <div className="text-muted">Geri dolduruldu: {r.backfilled.join(", ")}</div>
                      )}
                      {!r.error && !Object.keys(r.errors).length && !r.backfilled.length && (
                        <span className="text-muted">
                          {r.symbols_total} sembol{r.pid ? ` · pid ${r.pid}` : ""}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={PAGE_SIZE} total={q.data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ panel
function CollectorPanel({ name }: { name: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["collector", name],
    queryFn: () => api.get<CollectorHealth>(`/collectors/${name}`),
    refetchInterval: 10_000,
  });
  const run = useMutation({
    mutationFn: () => api.post(`/collectors/${name}/run`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["collector", name] });
      void qc.invalidateQueries({ queryKey: ["collector-runs", name] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });

  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data) return <Spinner />;
  const h = q.data;
  const list = issues(h);
  const st = h.stats_24h;
  const running = h.last_run?.status === "RUNNING" || run.isPending;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={COLLECTOR_TITLE[h.name] ?? h.title}
          icon={RadioTower}
          subtitle={h.source}
          actions={
            <>
              <HealthBadge status={h.status} />
              <button className="btn-ghost" disabled={running} onClick={() => run.mutate()}>
                <RefreshCw size={14} className={clsx(running && "animate-spin")} /> Şimdi çalıştır
              </button>
            </>
          }
        />
        <div className="space-y-3 p-4">
          {run.error && <Notice tone="loss">{(run.error as Error).message}</Notice>}
          {list.length > 0 ? (
            <Notice tone={h.status === "failing" ? "loss" : "warn"}>
              <ul className="space-y-0.5">
                {list.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </Notice>
          ) : (
            <div className="flex items-center gap-2 text-[12.5px] text-gain">
              <CircleCheck size={14} /> Bütün semboller güncel, son çalışma başarılı.
            </div>
          )}
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-muted">
            <span>
              Zamanlama: her saat :{String(h.schedule_minute).padStart(2, "0")}
            </span>
            <span>Saklama: {h.retention_days} gün (borsa ~{h.exchange_history_days} gün tutuyor)</span>
            <span>{h.symbols.length} sembol · {h.metrics.length} metrik</span>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Süreç"
          icon={Cpu}
          value={
            <span className="flex items-center gap-2 font-sans">
              <StatusDot on={h.process.running} pulse /> {h.process.running ? "Çalışıyor" : "Durdu"}
            </span>
          }
          hint={
            h.process.running ? (
              <span>
                pid {h.process.pid} · {h.process.started_at ? `${relative(h.process.started_at)} başladı` : ""}
              </span>
            ) : (
              <span>Süreçler sayfasından başlatın</span>
            )
          }
        />
        <KpiCard
          label="Son çalışma"
          icon={Clock3}
          value={<span className="font-sans">{h.last_run ? relative(h.last_run.started_at) : DASH}</span>}
          hint={
            h.last_run ? (
              <span className="flex items-center gap-1.5">
                <RunBadge status={h.last_run.status} /> {duration(h.last_run.duration_ms)}
              </span>
            ) : undefined
          }
        />
        <KpiCard
          label="Sonraki çalışma"
          icon={CalendarClock}
          value={h.next_run_at ? new Date(h.next_run_at).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" }) : DASH}
          hint={
            <span>
              Son başarılı: {h.last_success_at ? relative(h.last_success_at) : DASH}
            </span>
          }
        />
        <KpiCard
          label="Son 24 saat"
          icon={Hourglass}
          value={
            <span>
              {st.ok}
              <span className="text-muted">/{st.runs}</span>
            </span>
          }
          hint={
            <span>
              {st.partial ? `${st.partial} kısmi · ` : ""}
              {st.failed ? <span className="text-loss">{st.failed} başarısız · </span> : ""}+{n(st.rows_added)} satır
              {st.avg_duration_ms != null ? ` · ort. ${duration(st.avg_duration_ms)}` : ""}
            </span>
          }
        />
      </div>

      <Card>
        <CardHeader
          title="Veri tazeliği"
          icon={HardDrive}
          subtitle={
            <span>
              {n(h.data.rows)} satır
              {h.data.oldest ? ` · ${date(h.data.oldest)} → ${h.data.newest ? dateTime(h.data.newest) : DASH}` : ""}
              {h.data.missing_periods ? ` · ${n(h.data.missing_periods)} kayıp saat` : ""}
            </span>
          }
        />
        <FreshnessTable h={h} />
        <div className="border-t border-line/60 px-4 py-2.5 text-[11.5px] text-muted">
          Gecikme: beklenen son saatlik kayda göre geride kalınan saat (2 saati aşarsa bayat). Kayıp: ilk ve son kayıt arasında
          olmayan saat (borsada da eksik olabilir).
        </div>
      </Card>

      <RunsTable name={name} />

      <Card>
        <CardHeader title="Süreç logu" icon={Terminal} subtitle="logs/collector.log (son 120 satır)" />
        <div className="p-4">
          <LogView lines={h.log ?? []} />
        </div>
      </Card>
    </div>
  );
}

export function CollectorsPage() {
  const q = useQuery({ queryKey: ["collectors"], queryFn: () => api.get<CollectorHealth[]>("/collectors") });
  return (
    <>
      <PageHeader
        title="Toplayıcılar"
        icon={RadioTower}
        description="Veri toplama süreçlerinin sağlığı, veri tazeliği ve çalışma geçmişi"
      />
      {q.error && <ErrorState error={q.error} />}
      {q.isLoading && <Spinner />}
      <div className="space-y-8">
        {q.data?.map((c) => (
          <CollectorPanel key={c.name} name={c.name} />
        ))}
      </div>
    </>
  );
}
