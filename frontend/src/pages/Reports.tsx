import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowLeft,
  Bot,
  CalendarRange,
  DatabaseZap,
  DoorOpen,
  FileChartColumn,
  FileDown,
  FileText,
  History,
  LineChart,
  ListChecks,
  Plus,
  Printer,
  Send,
  ShieldAlert,
  Sheet,
  Table2,
  Trash2,
  UserCog,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, qs } from "../api/client";
import type { Overview, ReportData, ReportFull, ReportKind, ReportsPage as ReportsList } from "../api/types";
import { EquityChart } from "../components/charts";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Badge, Card, CardHeader, EmptyState, ErrorState, Field, Notice, PageHeader, Pagination, Pnl, Spinner } from "../components/ui";
import { actionLabel, DASH, date, dateTime, exitReason, money, pct, price, relative, signedMoney } from "../lib/format";
import type { Theme } from "../lib/theme";

const KIND_LABEL: Record<ReportKind, string> = { day: "Günlük", week: "Haftalık", month: "Aylık", custom: "Özel dönem" };
const KIND_TITLE: Record<ReportKind, string> = { day: "Günlük rapor", week: "Haftalık rapor", month: "Aylık rapor", custom: "Özel dönem raporu" };
const CREATOR: Record<string, string> = { schedule: "Zamanlanmış", cli: "CLI" };

const EVENT_LABEL: Record<string, string> = {
  TRADING_ENABLED: "Alım izni açıldı",
  TRADING_DISABLED: "Alım izni kapatıldı",
  KILL_SWITCH_TRIP: "Kill-switch tetiklendi",
  KILL_SWITCH_RESET: "Kill-switch sıfırlandı",
  FIRST_ORDER_CONFIRMED: "İlk emir onaylandı",
};


/** UTC tarih (YYYY-MM-DD) -> yerel gosterim; tarih sinirlari UTC oldugu icin UTC'de formatlanir. */
function utcDay(iso: string): string {
  return new Date(iso).toLocaleDateString("tr-TR", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

export function periodText(start: string, end: string): string {
  const last = new Date(new Date(end).getTime() - 86_400_000).toISOString();
  return start.slice(0, 10) === last.slice(0, 10) ? utcDay(start) : `${utcDay(start)} – ${utcDay(last)}`;
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

// ============================================================ liste + form
type Preset = "day" | "week" | "month" | "last30" | "custom";

export function ReportsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const size = 15;
  const list = useQuery({
    queryKey: ["reports", page],
    queryFn: () => api.get<ReportsList>(`/reports${qs({ limit: size, offset: (page - 1) * size })}`),
  });
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/overview") });
  const accounts = overview.data?.accounts.map((a) => a.name) ?? [];

  const today = isoDate(new Date());
  const [preset, setPreset] = useState<Preset>("week");
  const [start, setStart] = useState(isoDate(new Date(Date.now() - 7 * 86_400_000)));
  const [end, setEnd] = useState(isoDate(new Date(Date.now() - 86_400_000)));
  const [account, setAccount] = useState("");

  const create = useMutation({
    mutationFn: (body: unknown) => api.post<{ id: number }>("/reports", body),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["reports"] });
      navigate(`/reports/${r.id}`);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const acc = account || undefined;
    if (preset === "last30") {
      create.mutate({ kind: "custom", start: isoDate(new Date(Date.now() - 30 * 86_400_000)), end: today, account: acc });
    } else if (preset === "custom") {
      create.mutate({ kind: "custom", start, end, account: acc });
    } else {
      create.mutate({ kind: preset, account: acc });
    }
  };

  return (
    <>
      <PageHeader
        title="Raporlar"
        icon={FileChartColumn}
        description="Dönem raporları: oluşturulduğu anın donmuş görüntüsü — yazdırılabilir, Markdown/CSV olarak indirilebilir"
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Yeni rapor" icon={Plus} subtitle="Gün sınırları UTC" />
          <form onSubmit={submit} className="space-y-4 p-4">
            <Field label="Dönem">
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    ["day", "Dün"],
                    ["week", "Geçen hafta"],
                    ["month", "Geçen ay"],
                    ["last30", "Son 30 gün"],
                    ["custom", "Özel"],
                  ] as [Preset, string][]
                ).map(([v, l]) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setPreset(v)}
                    className={clsx(
                      "rounded-lg border px-2.5 py-1.5 text-[12px] font-medium transition",
                      preset === v ? "border-accent/60 bg-accent/10 text-fg" : "border-line text-muted hover:text-fg",
                    )}
                  >
                    {l}
                  </button>
                ))}
              </div>
            </Field>
            {preset === "custom" && (
              <div className="grid grid-cols-2 gap-3">
                <Field label="Başlangıç">
                  <input className="input num" type="date" value={start} max={today} onChange={(e) => setStart(e.target.value)} />
                </Field>
                <Field label="Bitiş (dahil)">
                  <input className="input num" type="date" value={end} max={today} onChange={(e) => setEnd(e.target.value)} />
                </Field>
              </div>
            )}
            <Field label="Hesap">
              <select className="input" value={account} onChange={(e) => setAccount(e.target.value)}>
                <option value="">Tüm hesaplar</option>
                {accounts.some((a) => a.startsWith("paper:")) && <option value="paper*">Tüm paper hesapları</option>}
                {accounts.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </Field>
            {create.error && <Notice tone="loss">{(create.error as Error).message}</Notice>}
            <button className="btn-primary w-full" disabled={create.isPending}>
              <FileText size={14} /> {create.isPending ? "Oluşturuluyor…" : "Raporu oluştur"}
            </button>
            <p className="text-[11.5px] text-muted">
              Paper runner her pazartesi 08:00 UTC'de geçen haftanın raporunu kendiliğinden oluşturur ve Telegram'a gönderir.
            </p>
          </form>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader title="Oluşturulan raporlar" icon={History} />
          {list.error && <ErrorState error={list.error} />}
          {list.isLoading && <Spinner />}
          {list.data && list.data.items.length === 0 && (
            <EmptyState icon={FileChartColumn} title="Henüz rapor yok">
              Soldan bir dönem seçip ilk raporu oluşturun.
            </EmptyState>
          )}
          {list.data && list.data.items.length > 0 && (
            <>
              <div className="overflow-x-auto">
                <table className="table-base">
                  <thead>
                    <tr>
                      <th>Rapor</th>
                      <th>Dönem</th>
                      <th>Hesap</th>
                      <th className="text-right">Net K/Z</th>
                      <th className="text-right">İşlem</th>
                      <th>Oluşturan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.data.items.map((r) => (
                      <tr key={r.id} className="cursor-pointer" onClick={() => navigate(`/reports/${r.id}`)}>
                        <td>
                          <div className="flex items-center gap-2 font-medium">
                            {KIND_LABEL[r.kind]}
                            {!r.complete && <Badge tone="warn">Sürüyor</Badge>}
                            {r.sent_at && <Send size={12} className="text-muted" aria-label="Telegram'a gönderildi" />}
                          </div>
                          <div className="num text-[11px] text-muted">#{r.id}</div>
                        </td>
                        <td className="text-[12.5px]">{periodText(r.period_start, r.period_end)}</td>
                        <td className="num text-muted">{r.account ?? "tümü"}</td>
                        <td className="text-right">
                          <Pnl value={r.realized} kind="money" />
                        </td>
                        <td className="num text-right">{r.closed}</td>
                        <td className="text-muted">
                          {CREATOR[r.created_by] ?? r.created_by} · {relative(r.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} size={size} total={list.data.total} onPage={setPage} />
            </>
          )}
        </Card>
      </div>
    </>
  );
}

// ============================================================ detay
function Kpi({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-line/70 bg-bg/40 px-3 py-2.5">
      <div className="label truncate">{label}</div>
      <div className="num mt-1 text-[17px] font-semibold leading-tight">{children}</div>
      {hint && <div className="mt-0.5 text-[11.5px] text-muted">{hint}</div>}
    </div>
  );
}

/** Gunluk K/Z: sifirdan yukari (kar) / asagi (zarar) cubuk; yon konumla kodlanir. */
export function DailyBars({ daily }: { daily: ReportData["daily"] }) {
  const max = Math.max(1e-9, ...daily.map((d) => Math.abs(d.pnl)));
  const H = 120;
  const showLabels = daily.length <= 31;
  return (
    <div className="px-4 pb-3 pt-2">
      <div className="flex items-stretch gap-[2px]" style={{ height: H }} role="img" aria-label="Günlük kâr/zarar çubukları">
        {daily.map((d) => {
          const h = (Math.abs(d.pnl) / max) * (H / 2 - 4);
          const tip = `${utcDay(d.date)}: ${signedMoney(d.pnl)} USDT · ${d.trades} işlem · kümülatif ${signedMoney(d.cum)}`;
          return (
            <div key={d.date} title={tip} className="group relative flex flex-1 flex-col hover:bg-raised/60">
              <div className="flex flex-1 items-end justify-center">
                {d.pnl > 0 && <div className="w-full max-w-[22px] rounded-t bg-gain/75" style={{ height: Math.max(h, 2) }} />}
              </div>
              <div className="h-px w-full bg-line" />
              <div className="flex flex-1 items-start justify-center">
                {d.pnl < 0 && <div className="w-full max-w-[22px] rounded-b bg-loss/75" style={{ height: Math.max(h, 2) }} />}
              </div>
            </div>
          );
        })}
      </div>
      {showLabels && (
        <div className="mt-1 flex gap-[2px] text-center text-[9.5px] text-muted">
          {daily.map((d, i) => (
            <span key={d.date} className="num flex-1 truncate">
              {daily.length <= 14 || i % Math.ceil(daily.length / 10) === 0 ? d.date.slice(8, 10) : ""}
            </span>
          ))}
        </div>
      )}
      <div className="mt-1 flex justify-between text-[10.5px] text-muted">
        <span>↑ kâr · ↓ zarar (gün başına, USDT)</span>
        <span className="num">en büyük gün: {money(max)}</span>
      </div>
    </div>
  );
}

export function ReportDetailPage({ theme }: { theme: Theme }) {
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["report", id], queryFn: () => api.get<ReportFull>(`/reports/${id}`), refetchInterval: false });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const send = useMutation({
    mutationFn: () => api.post(`/reports/${id}/send`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["report", id] }),
  });
  const del = useMutation({
    mutationFn: () => api.delete(`/reports/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reports"] });
      navigate("/reports");
    },
  });

  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data) return <Spinner />;
  const r = q.data;
  const d = r.data;
  const s = d.summary;

  return (
    <>
      <div className="no-print mb-3">
        <Link to="/reports" className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg">
          <ArrowLeft size={14} /> Raporlar
        </Link>
      </div>
      <PageHeader
        title={KIND_TITLE[r.kind]}
        icon={FileChartColumn}
        description={
          <span>
            {periodText(r.period_start, r.period_end)} (UTC) · {r.account ?? "tüm hesaplar"} · #{r.id} · {dateTime(d.generated_at)} tarihinde{" "}
            {CREATOR[r.created_by] ?? r.created_by} tarafından oluşturuldu
          </span>
        }
        actions={
          <div className="no-print flex flex-wrap gap-2">
            <button className="btn-ghost" onClick={() => window.print()}>
              <Printer size={14} /> Yazdır / PDF
            </button>
            <a className="btn-ghost" href={`/api/reports/${r.id}/download?format=md`} download>
              <FileDown size={14} /> Markdown
            </a>
            <a className="btn-ghost" href={`/api/reports/${r.id}/download?format=csv`} download>
              <Sheet size={14} /> İşlemler CSV
            </a>
            <button className="btn-ghost" disabled={send.isPending} onClick={() => send.mutate()}>
              <Send size={14} /> {r.sent_at ? "Tekrar gönder" : "Telegram'a gönder"}
            </button>
            <button className="btn-ghost text-loss" onClick={() => setConfirmDelete(true)}>
              <Trash2 size={14} /> Sil
            </button>
          </div>
        }
      />
      <div className="no-print space-y-2">
        {!d.period.complete && <Notice>Dönem henüz bitmedi: bu rapor {dateTime(d.generated_at)} anındaki durumu gösteriyor.</Notice>}
        {send.error && <Notice tone="loss">{(send.error as Error).message}</Notice>}
        {send.isSuccess && <Notice tone="accent">Özet Telegram'a gönderildi.</Notice>}
      </div>

      <Card className="mt-3">
        <CardHeader title="Özet" icon={ListChecks} subtitle="Dönemde kapanan işlemler · komisyonlar düşülmüş" />
        <div className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4 2xl:grid-cols-8">
          <Kpi label="Net K/Z" hint={`${money(s.fees)} komisyon`}>
            <Pnl value={s.realized} kind="money" />
          </Kpi>
          <Kpi label="Kapanan işlem" hint={`${s.wins} kazanç · ${s.losses} kayıp`}>
            {s.closed}
          </Kpi>
          <Kpi label="Kazanma oranı">{s.win_rate == null ? DASH : pct(s.win_rate, 0, false)}</Kpi>
          <Kpi label="Kâr faktörü" hint="brüt kazanç / brüt kayıp">
            {s.profit_factor == null ? (s.wins ? "∞" : DASH) : money(s.profit_factor)}
          </Kpi>
          <Kpi label="İşlem başına" hint="beklenti">
            {s.expectancy == null ? DASH : signedMoney(s.expectancy)}
          </Kpi>
          <Kpi label="Ort. kazanç / kayıp">
            <span className="text-[14px]">
              {s.avg_win == null ? DASH : signedMoney(s.avg_win)} / {s.avg_loss == null ? DASH : signedMoney(s.avg_loss)}
            </span>
          </Kpi>
          <Kpi label="En uzun kayıp serisi">{s.longest_losing_streak}</Kpi>
          <Kpi label="Açılan / dönem sonu açık">
            {s.opened} / {s.open_at_end}
          </Kpi>
        </div>
        {(s.best || s.worst) && (
          <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-line/60 px-4 py-2.5 text-[12.5px] text-muted">
            {s.best && (
              <span>
                En iyi işlem: <span className="text-fg">{s.best.symbol}</span> <Pnl value={s.best.pnl} kind="money" /> ({dateTime(s.best.exit_time)})
              </span>
            )}
            {s.worst && (
              <span>
                En kötü işlem: <span className="text-fg">{s.worst.symbol}</span> <Pnl value={s.worst.pnl} kind="money" /> ({dateTime(s.worst.exit_time)})
              </span>
            )}
          </div>
        )}
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Günlük K/Z" icon={CalendarRange} subtitle={`${d.daily.length} gün · işlem olan gün: ${d.daily.filter((x) => x.trades).length}`} />
          {s.closed ? <DailyBars daily={d.daily} /> : <EmptyState icon={CalendarRange} title="Bu dönemde kapanan işlem yok" />}
        </Card>
        <Card>
          <CardHeader title="Hesap değeri" icon={LineChart} subtitle="Runner'ın tick başına kaydettiği equity" />
          {d.equity.length === 0 ? (
            <EmptyState icon={LineChart} title="Bu dönemde equity kaydı yok">
              Paper runner çalıştığında her tick'te kaydedilir.
            </EmptyState>
          ) : (
            <>
              {d.equity.length === 1 && d.equity[0]!.points.length > 1 && (
                <div className="p-3">
                  <EquityChart theme={theme} height={170} points={d.equity[0]!.points} baseline={d.equity[0]!.start} />
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="table-base">
                  <thead>
                    <tr>
                      <th>Hesap</th>
                      <th className="text-right">Başlangıç</th>
                      <th className="text-right">Bitiş</th>
                      <th className="text-right">Getiri</th>
                      <th className="text-right">Maks. düşüş</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.equity.map((e) => (
                      <tr key={e.account}>
                        <td className="num">{e.account}</td>
                        <td className="num text-right">{money(e.start)}</td>
                        <td className="num text-right">{money(e.end)}</td>
                        <td className="text-right">
                          <Pnl value={e.return_pct} />
                        </td>
                        <td className="num text-right">{pct(e.max_drawdown, 2, false)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      </div>

      {(d.by_symbol.length > 0 || d.by_reason.length > 0) && (
        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader title="Sembol bazında" icon={Table2} />
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Sembol</th>
                    <th className="text-right">İşlem</th>
                    <th className="text-right">Kazanç</th>
                    <th className="text-right">Komisyon</th>
                    <th className="text-right">K/Z</th>
                  </tr>
                </thead>
                <tbody>
                  {d.by_symbol.map((x) => (
                    <tr key={x.symbol}>
                      <td className="font-medium">{x.symbol}</td>
                      <td className="num text-right">{x.trades}</td>
                      <td className="num text-right">{x.wins}</td>
                      <td className="num text-right text-muted">{money(x.fees)}</td>
                      <td className="text-right">
                        <Pnl value={x.pnl} kind="money" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card>
            <CardHeader title="Çıkış nedenleri" icon={DoorOpen} />
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Neden</th>
                    <th className="text-right">İşlem</th>
                    <th className="text-right">Kazanç</th>
                    <th className="text-right">K/Z</th>
                  </tr>
                </thead>
                <tbody>
                  {d.by_reason.map((x) => (
                    <tr key={x.reason}>
                      <td>{exitReason(x.reason)}</td>
                      <td className="num text-right">{x.count}</td>
                      <td className="num text-right">{x.wins}</td>
                      <td className="text-right">
                        <Pnl value={x.pnl} kind="money" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}

      <Card className="mt-4 print-break">
        <CardHeader
          title="Kapanan işlemler"
          icon={ListChecks}
          subtitle={d.trades_truncated ? `${d.trades_truncated} eski işlem rapora sığmadı` : `${d.trades.length} işlem`}
        />
        {d.trades.length === 0 ? (
          <EmptyState icon={ListChecks} title="İşlem yok" />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Çıkış</th>
                  <th>Hesap</th>
                  <th>Sembol</th>
                  <th className="text-right">Giriş</th>
                  <th className="text-right">Çıkış</th>
                  <th className="text-right">Getiri</th>
                  <th className="text-right">K/Z</th>
                  <th>Neden</th>
                </tr>
              </thead>
              <tbody>
                {[...d.trades].reverse().map((t) => (
                  <tr key={t.id}>
                    <td className="num text-[12px]">{dateTime(t.exit_time)}</td>
                    <td className="num text-muted">{t.account}</td>
                    <td className="font-medium">{t.symbol}</td>
                    <td className="num text-right">{price(t.entry_price)}</td>
                    <td className="num text-right">{price(t.exit_price)}</td>
                    <td className="text-right">
                      <Pnl value={t.return_pct} />
                    </td>
                    <td className="text-right">
                      <Pnl value={t.pnl} kind="money" />
                    </td>
                    <td className="text-muted">{exitReason(t.exit_reason)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Risk olayları" icon={ShieldAlert} />
          {d.risk_events.length === 0 ? (
            <div className="px-4 py-4 text-[12.5px] text-muted">Bu dönemde risk olayı yok.</div>
          ) : (
            <ul className="divide-y divide-line/50 text-[12.5px]">
              {d.risk_events.map((e, i) => (
                <li key={i} className="px-4 py-2">
                  <div className="flex justify-between gap-2">
                    <span className="font-medium">{EVENT_LABEL[e.type] ?? e.type}</span>
                    <span className="num text-muted">{e.account}</span>
                  </div>
                  <div className="text-[11.5px] text-muted">
                    {dateTime(e.ts)}
                    {e.reason ? ` · ${e.reason}` : ""}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Veri toplayıcı" icon={DatabaseZap} />
          <div className="space-y-1 p-4 text-[12.5px]">
            {d.collector.runs === 0 ? (
              <span className="text-muted">Bu dönemde çalışma kaydı yok.</span>
            ) : (
              <>
                <div className="flex justify-between">
                  <span className="text-muted">Çalışma</span>
                  <span className="num">{d.collector.runs}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Başarılı / kısmi / başarısız</span>
                  <span className={clsx("num", d.collector.failed > 0 && "text-loss")}>
                    {d.collector.ok} / {d.collector.partial} / {d.collector.failed}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Eklenen / silinen satır</span>
                  <span className="num">
                    +{d.collector.rows_added.toLocaleString("tr-TR")} / −{d.collector.rows_purged.toLocaleString("tr-TR")}
                  </span>
                </div>
              </>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Panel işlemleri" icon={UserCog} subtitle="Son 200" />
          {d.admin_actions.length === 0 ? (
            <div className="px-4 py-4 text-[12.5px] text-muted">Bu dönemde panel işlemi yok.</div>
          ) : (
            <ul className="max-h-72 divide-y divide-line/50 overflow-auto text-[12.5px]">
              {d.admin_actions.map((a, i) => (
                <li key={i} className="flex items-center justify-between gap-2 px-4 py-1.5">
                  <span className={clsx(!a.ok && "text-loss")}>
                    {actionLabel(a.action)}
                    {a.account ? <span className="num text-muted"> · {a.account}</span> : null}
                  </span>
                  <span className="shrink-0 text-[11.5px] text-muted">
                    <Bot size={11} className="mr-1 inline" />
                    {a.username} · {date(a.ts)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Rapor silinsin mi?"
        description="Rapor kalıcı olarak silinir; aynı dönem için yeniden oluşturulabilir (o andaki veriyle)."
        confirmText={String(r.id)}
        actionLabel="Sil"
        danger
        busy={del.isPending}
        error={del.error ? (del.error as Error).message : null}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => del.mutate()}
      />
    </>
  );
}
