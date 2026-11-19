import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Ban,
  BookmarkPlus,
  CircleCheck,
  CircleX,
  Dices,
  FlaskConical,
  Hourglass,
  LineChart,
  ListChecks,
  Loader2,
  Play,
  Sigma,
  Table2,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { BacktestRun, BaselineInfo, Metrics, ProfileInfo } from "../api/types";
import { EquityChart } from "../components/charts";
import { Badge, Card, CardHeader, EmptyState, ErrorState, Field, KpiCard, Notice, PageHeader, Pnl, Spinner, Stat } from "../components/ui";
import { dateTime, exitReason, money, pct, relative } from "../lib/format";
import type { Theme } from "../lib/theme";

function StatusBadge({ status }: { status: BacktestRun["status"] }) {
  switch (status) {
    case "DONE":
      return <Badge tone="gain" icon={CircleCheck}>Tamamlandı</Badge>;
    case "FAILED":
      return <Badge tone="loss" icon={CircleX}>Hata</Badge>;
    case "RUNNING":
      return <Badge tone="accent" icon={Loader2}>Çalışıyor</Badge>;
    case "CANCELLED":
      return <Badge icon={Ban}>İptal edildi</Badge>;
    default:
      return <Badge icon={Hourglass}>Sırada</Badge>;
  }
}

function GateBadge({ passed }: { passed: boolean }) {
  return passed ? <Badge tone="gain" icon={CircleCheck}>Kapı geçti</Badge> : <Badge tone="warn" icon={CircleX}>Kapı kaldı</Badge>;
}

export function BacktestsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => api.get<ProfileInfo[]>("/profiles"), refetchInterval: false });
  const runs = useQuery({
    queryKey: ["backtests"],
    queryFn: () => api.get<BacktestRun[]>("/backtests"),
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === "RUNNING" || r.status === "QUEUED") ? 3_000 : 15_000),
  });
  const baselines = useQuery({ queryKey: ["baselines"], queryFn: () => api.get<BaselineInfo[]>("/baselines") });
  const cancel = useMutation({
    mutationFn: (id: number) => api.post(`/backtests/${id}/cancel`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["backtests"] }),
  });

  const [form, setForm] = useState({
    profile: "donchian_fng",
    symbols: "BTC/USDT,ETH/USDT,SOL/USDT",
    timeframe: "4h",
    days: 1095,
    train_days: 180,
    test_days: 60,
    sensitivity: false,
    slippage_bps: 5,
  });
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));

  const start = useMutation({
    mutationFn: () =>
      api.post<{ id: number }>("/backtests", {
        ...form,
        symbols: form.symbols.split(",").map((s) => s.trim()).filter(Boolean),
        optimize: false,
      }),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ["backtests"] });
      navigate(`/backtests/${res.id}`);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    start.mutate();
  };

  return (
    <>
      <PageHeader
        title="Backtest"
        icon={FlaskConical}
        description="Walk-forward out-of-sample + Monte Carlo + onay kapısı. Holdout panelden çalıştırılamaz."
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Yeni çalıştırma" icon={Play} />
          <form onSubmit={submit} className="space-y-3 p-4">
            <Field label="Profil" hint={profiles.data?.find((p) => p.name === form.profile)?.description}>
              <select className="input" value={form.profile} onChange={(e) => set("profile", e.target.value)}>
                {profiles.data?.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Semboller">
              <textarea className="input num min-h-[56px]" value={form.symbols} onChange={(e) => set("symbols", e.target.value)} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Zaman dilimi">
                <select className="input" value={form.timeframe} onChange={(e) => set("timeframe", e.target.value)}>
                  {["1h", "4h", "1d"].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </Field>
              <Field label="Geçmiş (gün)" hint="son %30 holdout">
                <input className="input num" type="number" value={form.days} onChange={(e) => set("days", Number(e.target.value))} />
              </Field>
              <Field label="Eğitim (gün)">
                <input className="input num" type="number" value={form.train_days} onChange={(e) => set("train_days", Number(e.target.value))} />
              </Field>
              <Field label="Test (gün)">
                <input className="input num" type="number" value={form.test_days} onChange={(e) => set("test_days", Number(e.target.value))} />
              </Field>
              <Field label="Kayma (bps)">
                <input className="input num" type="number" value={form.slippage_bps} onChange={(e) => set("slippage_bps", Number(e.target.value))} />
              </Field>
              <label className="flex items-end gap-2 pb-2 text-[12.5px]">
                <input type="checkbox" checked={form.sensitivity} onChange={(e) => set("sensitivity", e.target.checked)} />
                Hassasiyet (yavaş)
              </label>
            </div>
            {start.error && <Notice tone="loss">{(start.error as Error).message}</Notice>}
            <button className="btn-primary w-full" disabled={start.isPending}>
              <Play size={14} /> Çalıştır
            </button>
          </form>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader title="Çalıştırmalar" icon={ListChecks} subtitle="Sıradaki iş iptal edilebilir; çalışan iş yarıda kesilemez" />
          {runs.error && <ErrorState error={runs.error} />}
          {cancel.error && (
            <div className="p-3">
              <Notice tone="loss">{(cancel.error as Error).message}</Notice>
            </div>
          )}
          {runs.data && runs.data.length === 0 && <EmptyState icon={FlaskConical} title="Henüz backtest yok" />}
          {runs.data && runs.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Profil</th>
                    <th>Kapsam</th>
                    <th>Durum</th>
                    <th className="text-right">İşlem</th>
                    <th className="text-right">PF</th>
                    <th className="text-right">Sharpe</th>
                    <th>Kapı</th>
                    <th>Zaman</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {runs.data.map((r) => (
                    <tr key={r.id} className="cursor-pointer" onClick={() => navigate(`/backtests/${r.id}`)}>
                      <td className="num text-muted">{r.id}</td>
                      <td className="font-medium">{r.params.profile}</td>
                      <td className="num text-[12px] text-muted">
                        {r.params.timeframe} · {r.params.symbols.length} sembol · {r.params.days}g
                      </td>
                      <td>
                        <StatusBadge status={r.status} />
                      </td>
                      <td className="num text-right">{r.summary?.pooled.n_trades ?? "—"}</td>
                      <td className="num text-right">{r.summary ? money(r.summary.pooled.profit_factor) : "—"}</td>
                      <td className="num text-right">{r.summary ? money(r.summary.pooled.sharpe) : "—"}</td>
                      <td>{r.summary ? <GateBadge passed={r.summary.gate_passed} /> : "—"}</td>
                      <td className="text-muted">
                        {relative(r.created_at)} · {r.created_by}
                      </td>
                      <td className="text-right">
                        {r.status === "QUEUED" && (
                          <button
                            className="btn-ghost px-2 py-1 text-[12px]"
                            disabled={cancel.isPending}
                            onClick={(e) => {
                              e.stopPropagation();
                              cancel.mutate(r.id);
                            }}
                            title="Sıradaki işi iptal et"
                          >
                            <Ban size={13} /> İptal
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Drift baseline'ları" icon={BookmarkPlus} subtitle="Canlı performans bu dağılımla karşılaştırılır" />
        {baselines.data && baselines.data.length === 0 && <EmptyState icon={BookmarkPlus} title="Kayıtlı baseline yok" />}
        {baselines.data && baselines.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Profil</th>
                  <th>TF</th>
                  <th className="text-right">OOS işlem</th>
                  <th className="text-right">Ort. getiri</th>
                  <th>Kapı</th>
                  <th>Oluşturma</th>
                </tr>
              </thead>
              <tbody>
                {baselines.data.map((b) => (
                  <tr key={b.file}>
                    <td className="font-medium">{b.profile}</td>
                    <td className="num">{b.timeframe}</td>
                    <td className="num text-right">{b.n}</td>
                    <td className="text-right">
                      <Pnl value={b.mean} />
                    </td>
                    <td>
                      <GateBadge passed={b.gate_passed} />
                    </td>
                    <td className="text-muted">{dateTime(b.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

const CHECK_LABEL: Record<string, string> = {
  min_trades: "En az 100 OOS işlem",
  profit_factor: "Profit factor > 1.3",
  sharpe: "Sharpe > 1",
  mc_drawdown: "Monte Carlo p95 DD < %15",
  symbols: "Sembollerin 2/3'u pozitif",
  sensitivity: "±%20 parametrede çökme yok",
};

function MetricsRow({ symbol, m }: { symbol: string; m: Metrics }) {
  return (
    <tr>
      <td className="font-medium">{symbol}</td>
      <td className="num text-right">{m.n_trades}</td>
      <td className="num text-right">{pct(m.win_rate, 1, false)}</td>
      <td className="num text-right">{money(m.profit_factor)}</td>
      <td className="text-right">
        <Pnl value={m.expectancy_pct} />
      </td>
      <td className="text-right">
        <Pnl value={m.total_return} />
      </td>
      <td className="num text-right">{pct(m.max_drawdown, 1, false)}</td>
      <td className="num text-right">{money(m.sharpe)}</td>
    </tr>
  );
}

export function BacktestDetailPage({ theme }: { theme: Theme }) {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["backtest", id],
    queryFn: () => api.get<BacktestRun>(`/backtests/${id}`),
    refetchInterval: (query) => (query.state.data && ["RUNNING", "QUEUED"].includes(query.state.data.status) ? 3_000 : false),
  });
  const save = useMutation({
    mutationFn: () => api.post<{ n: number; file: string }>(`/backtests/${id}/save-baseline`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["baselines"] }),
  });

  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data) return <Spinner />;
  const run = q.data;
  const r = run.result;

  return (
    <>
      <Link to="/backtests" className="mb-3 inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg">
        <ArrowLeft size={14} /> Backtest
      </Link>
      <PageHeader
        title={`${run.params.profile} #${run.id}`}
        icon={FlaskConical}
        description={
          <span className="num">
            {run.params.timeframe} · {run.params.symbols.join(", ")} · {run.params.days} gün · eğitim {run.params.train_days}g / test {run.params.test_days}g
          </span>
        }
        actions={
          <>
            <StatusBadge status={run.status} />
            {r && <GateBadge passed={r.gate.passed} />}
            {run.status === "DONE" && !run.params.profile.includes("+") && (
              <button className="btn-ghost" onClick={() => save.mutate()} disabled={save.isPending}>
                <BookmarkPlus size={14} /> {save.data ? `Kaydedildi (${save.data.n})` : "Drift baseline'i olarak kaydet"}
              </button>
            )}
          </>
        }
      />
      {save.error && (
        <div className="mb-4">
          <Notice tone="loss">{(save.error as Error).message}</Notice>
        </div>
      )}
      {run.status === "FAILED" && <Notice tone="loss">{run.error}</Notice>}
      {run.status === "CANCELLED" && <Notice>Bu backtest sırada beklerken iptal edildi.</Notice>}
      {(run.status === "RUNNING" || run.status === "QUEUED") && (
        <Card>
          <Spinner label={run.status === "QUEUED" ? "Sırada bekliyor" : "Veri yükleniyor ve walk-forward çalışıyor..."} />
        </Card>
      )}

      {r && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard label="OOS işlem" icon={Sigma} value={r.pooled.n_trades} hint={`kazanma ${pct(r.pooled.win_rate, 1, false)}`} />
            <KpiCard label="Profit factor" icon={LineChart} value={money(r.pooled.profit_factor)} hint={`Sharpe ${money(r.pooled.sharpe)}`} />
            <KpiCard label="Toplam getiri" icon={LineChart} value={<Pnl value={r.pooled.total_return} size="lg" />} hint={`max DD ${pct(r.pooled.max_drawdown, 1, false)}`} />
            <KpiCard
              label="Monte Carlo p95 DD"
              icon={Dices}
              value={pct(r.monte_carlo.max_dd_p95, 1, false)}
              hint={`P(zarar) ${pct(r.monte_carlo.prob_loss, 0, false)}`}
            />
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader title="OOS equity (sembol ortalaması, normalize)" icon={LineChart} />
              <div className="p-3">
                {r.equity.length > 1 ? <EquityChart theme={theme} points={r.equity} baseline={1} height={300} /> : <EmptyState title="Equity yok" />}
              </div>
            </Card>
            <Card>
              <CardHeader title="Onay kapısı" icon={ListChecks} />
              <ul className="space-y-2 p-4">
                {Object.entries(r.gate.checks).map(([k, ok]) => (
                  <li key={k} className="flex items-center gap-2 text-[13px]">
                    {ok ? <CircleCheck size={16} className="text-gain" /> : <CircleX size={16} className="text-loss" />}
                    <span className={ok ? "" : "text-muted"}>{CHECK_LABEL[k] ?? k}</span>
                  </li>
                ))}
              </ul>
              {r.gate.failures.length > 0 && (
                <div className="border-t border-line/60 p-4 text-[12px] text-muted">
                  {r.gate.failures.map((f) => (
                    <div key={f}>• {f}</div>
                  ))}
                </div>
              )}
              <div className="border-t border-line/60 p-4">
                <Stat label="MC getiri p5 / p50">
                  {pct(r.monte_carlo.total_return_p5)} / {pct(r.monte_carlo.total_return_p50)}
                </Stat>
                <Stat label="Hassasiyet (en kötü)">{r.sensitivity_worst === null ? "—" : money(r.sensitivity_worst)}</Stat>
              </div>
            </Card>
          </div>

          <Card className="mt-4">
            <CardHeader title="Sembol bazında" icon={Table2} />
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Sembol</th>
                    <th className="text-right">İşlem</th>
                    <th className="text-right">Kazanma</th>
                    <th className="text-right">PF</th>
                    <th className="text-right">Beklenti</th>
                    <th className="text-right">Getiri</th>
                    <th className="text-right">Max DD</th>
                    <th className="text-right">Sharpe</th>
                  </tr>
                </thead>
                <tbody>
                  {r.per_symbol.map((s) => (
                    <MetricsRow key={s.symbol} symbol={s.symbol} m={s.metrics} />
                  ))}
                  <tr className="bg-raised/40 font-semibold">
                    <td>Havuz</td>
                    <td className="num text-right">{r.pooled.n_trades}</td>
                    <td className="num text-right">{pct(r.pooled.win_rate, 1, false)}</td>
                    <td className="num text-right">{money(r.pooled.profit_factor)}</td>
                    <td className="text-right">
                      <Pnl value={r.pooled.expectancy_pct} />
                    </td>
                    <td className="text-right">
                      <Pnl value={r.pooled.total_return} />
                    </td>
                    <td className="num text-right">{pct(r.pooled.max_drawdown, 1, false)}</td>
                    <td className="num text-right">{money(r.pooled.sharpe)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Card>

          <Card className="mt-4">
            <CardHeader title="OOS işlemler" icon={Table2} subtitle={`${r.n_trades_total} işlem${r.n_trades_total > r.trades.length ? ` (son ${r.trades.length})` : ""}`} />
            <div className="max-h-[480px] overflow-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Sembol</th>
                    <th>Giriş</th>
                    <th>Çıkış</th>
                    <th className="text-right">Bar</th>
                    <th className="text-right">Fiyat getirisi</th>
                    <th className="text-right">Hesaba etkisi</th>
                    <th>Neden</th>
                  </tr>
                </thead>
                <tbody>
                  {[...r.trades].reverse().map((t, i) => (
                    <tr key={i}>
                      <td className="font-medium">{t.symbol}</td>
                      <td className="text-muted">{dateTime(t.entry_time)}</td>
                      <td className="text-muted">{dateTime(t.exit_time)}</td>
                      <td className="num text-right">{t.bars_held}</td>
                      <td className="text-right">
                        <Pnl value={t.return_pct} />
                      </td>
                      <td className="text-right">
                        <Pnl value={t.pnl_pct} />
                      </td>
                      <td className="text-muted">{exitReason(t.exit_reason)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
