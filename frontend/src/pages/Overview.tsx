import { useQuery } from "@tanstack/react-query";
import {
  AlertOctagon,
  ArrowLeftRight,
  BadgeDollarSign,
  Banknote,
  CandlestickChart,
  LayoutDashboard,
  LineChart,
  PieChart,
  TrendingUp,
  TriangleAlert,
  Wallet,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, qs } from "../api/client";
import type { EquityRow, Overview, TradesPage } from "../api/types";
import { EquityChart } from "../components/charts";
import { BuyPermissionBadge, DailyTargetProgress, DriftBadge, ModeBadge } from "../components/domain";
import { PnlDashboard } from "../components/PnlDashboard";
import { Sparkline } from "../components/Sparkline";
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KpiCard,
  PageHeader,
  Pnl,
  Segmented,
  Skeleton,
} from "../components/ui";
import { dateTime, exitReason, money, pct, relative, usd } from "../lib/format";
import type { Theme } from "../lib/theme";

type Range = "7" | "30" | "90" | "0";

const COLLECTOR_ALERT: Record<string, string> = {
  failing: "Veri toplayıcı başarısız: veri güncellenmiyor",
  stopped: "Veri toplayıcı süreci durdu",
  degraded: "Veri toplayıcıda sorun var",
};

export function OverviewPage({ theme }: { theme: Theme }) {
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/overview") });
  const accounts = overview.data?.accounts ?? [];
  const primary = accounts.find((a) => a.snapshot) ?? accounts[0];
  const [range, setRange] = useState<Range>("30");

  const equity = useQuery({
    queryKey: ["equity", primary?.name, range],
    queryFn: () => api.get<EquityRow[]>(`/accounts/${encodeURIComponent(primary!.name)}/equity${qs({ days: range })}`),
    enabled: !!primary,
  });
  const trades = useQuery({
    queryKey: ["trades", "recent"],
    queryFn: () => api.get<TradesPage>(`/trades${qs({ status: "CLOSED", size: 8 })}`),
  });

  if (overview.error) return <ErrorState error={overview.error} />;
  const t = overview.data?.totals;

  return (
    <>
      <PageHeader
        title="Genel Bakış"
        icon={LayoutDashboard}
        description={overview.data ? `Güncellendi: ${relative(overview.data.generated_at)}` : "Yükleniyor"}
      />

      {overview.data && overview.data.alerts.length > 0 && (
        <div className="mb-5 space-y-2">
          {overview.data.alerts.map((a, i) => (
            <div
              key={i}
              className={
                a.level === "critical"
                  ? "flex items-center gap-2.5 rounded-xl border border-loss/30 bg-loss/10 px-4 py-2.5 text-[13px] text-loss"
                  : "flex items-center gap-2.5 rounded-xl border border-warn/30 bg-warn/10 px-4 py-2.5 text-[13px] text-warn"
              }
            >
              {a.level === "critical" ? <AlertOctagon size={16} /> : <TriangleAlert size={16} />}
              {a.account && <span className="num font-medium">{a.account}</span>}
              {a.source?.startsWith("collector:") ? (
                <Link to="/collectors" className="underline-offset-2 hover:underline">
                  {COLLECTOR_ALERT[overview.data.collectors?.[a.source.slice(10)] ?? ""] ?? "Veri toplayıcıda sorun var"} — ayrıntılar
                </Link>
              ) : (
                <span>{a.text}</span>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {t ? (
          <>
            <KpiCard
              label="Portföy değeri"
              icon={Wallet}
              value={<>{money(t.equity)} <span className="text-[13px] text-muted">USDT</span></>}
              delta={t.return_since_start}
              hint={<span>başlangıçtan beri</span>}
              spark={equity.data?.map((r) => r.equity)}
            />
            <KpiCard
              label="Kullanılabilir nakit"
              icon={Banknote}
              value={money(t.cash)}
              hint={<span>{t.equity > 0 ? pct(t.cash / t.equity, 1, false) : "—"} portföyün</span>}
            />
            <KpiCard
              label="Pozisyondaki değer"
              icon={PieChart}
              value={money(t.market_value)}
              hint={
                <span>
                  {t.positions} açık pozisyon · tavan {t.exposure_cap == null ? "—" : pct(t.exposure_cap, 0, false)} · kullanılan {pct(t.exposure, 1, false)}
                </span>
              }
            />
            <KpiCard
              label="Gerçekleşmemiş K/Z"
              icon={BadgeDollarSign}
              value={<Pnl value={t.unrealized_pnl} kind="money" size="lg" />}
              hint={<span>açık pozisyonların toplamı</span>}
            />
          </>
        ) : (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-[118px] rounded-xl" />)
        )}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Portföy değeri"
            icon={LineChart}
            subtitle={primary ? `${primary.name} · tick başına kayıt` : undefined}
            actions={
              <Segmented<Range>
                value={range}
                onChange={setRange}
                options={[
                  { value: "7", label: "7G" },
                  { value: "30", label: "30G" },
                  { value: "90", label: "90G" },
                  { value: "0", label: "Tümü" },
                ]}
              />
            }
          />
          <div className="p-3">
            {equity.data && equity.data.length > 1 ? (
              <EquityChart
                theme={theme}
                points={equity.data.map((r) => ({ t: r.t, v: r.equity }))}
                baseline={primary?.initial_capital}
              />
            ) : (
              <EmptyState icon={TrendingUp} title="Henüz equity kaydı yok">
                Runner her tick'te hesap durumunu kaydeder. Süreçler sayfasından paper runner'i başlatın.
              </EmptyState>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Son kapanan işlemler" icon={ArrowLeftRight} actions={<Link to="/trades" className="text-[12px] text-accent">Tümü</Link>} />
          {trades.data && trades.data.items.length > 0 ? (
            <ul className="divide-y divide-line/50">
              {trades.data.items.map((tr) => (
                <li key={tr.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium">{tr.symbol}</div>
                    <div className="text-[11.5px] text-muted">
                      {exitReason(tr.exit_reason)} · {dateTime(tr.exit_time)}
                    </div>
                  </div>
                  <div className="text-right">
                    <Pnl value={tr.realized_pnl} kind="money" />
                    <div className="text-[11.5px]">
                      <Pnl value={tr.return_pct} />
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={ArrowLeftRight} title="Kapanan işlem yok" />
          )}
        </Card>
      </div>

      <PnlDashboard theme={theme} accounts={accounts.map((a) => a.name)} />

      <Card className="mt-4">
        <CardHeader title="Hesaplar" icon={Wallet} subtitle="Her hesabın bakiyesi, izinleri ve günlük zararı ayrıdır" />
        {accounts.length === 0 ? (
          <EmptyState icon={CandlestickChart} title="Kayıtlı hesap yok">
            Paper runner ilk kez başlatıldığında hesap başlangıç sermayesiyle kaydedilir.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Hesap</th>
                  <th>Profil</th>
                  <th className="text-right">Değer</th>
                  <th className="text-right">Bugün</th>
                  <th className="w-36">30 gün</th>
                  <th>Alım</th>
                  <th>Performans</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={a.name}>
                    <td>
                      <Link to={`/accounts/${encodeURIComponent(a.name)}`} className="flex items-center gap-2 font-medium hover:text-accent">
                        <span className="num">{a.name}</span>
                        <ModeBadge mode={a.mode} />
                      </Link>
                    </td>
                    <td className="text-muted">
                      {a.profile ?? "—"} {a.timeframe && <span className="num">· {a.timeframe}</span>}
                    </td>
                    <td className="num text-right">{a.snapshot ? usd(a.snapshot.equity) : "—"}</td>
                    <td className="text-right">
                      <Pnl value={a.pnl_today.total_pct} />
                      <DailyTargetProgress total={a.pnl_today.total_pct} target={a.pnl_today.target_pct} />
                    </td>
                    <td>
                      <div className="h-8 w-32">
                        <Sparkline values={a.sparkline.map((p) => p.v)} height={32} />
                      </div>
                    </td>
                    <td>
                      <BuyPermissionBadge account={a} />
                    </td>
                    <td>
                      <DriftBadge drift={a.drift} />
                    </td>
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
