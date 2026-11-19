import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Banknote,
  CandlestickChart,
  Gauge,
  LineChart,
  PieChart,
  ShieldAlert,
  Wallet,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, qs } from "../api/client";
import type { Account, EquityRow } from "../api/types";
import { EquityChart } from "../components/charts";
import { BuyPermissionBadge, DriftBadge, DriftDetail, ModeBadge } from "../components/domain";
import { RiskActions } from "../components/RiskActions";
import { Sparkline } from "../components/Sparkline";
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KpiCard,
  Notice,
  PageHeader,
  Pnl,
  Segmented,
  Spinner,
  Stat,
} from "../components/ui";
import { date, money, pct, usd } from "../lib/format";
import type { Theme } from "../lib/theme";
import { PositionsTable } from "./Positions";

export function AccountsPage() {
  const q = useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/accounts") });
  return (
    <>
      <PageHeader title="Hesaplar" icon={Wallet} description="Paper kök hesap, portföy alt hesapları ve canlı hesap" />
      {q.error && <ErrorState error={q.error} />}
      {q.isLoading && <Spinner />}
      {q.data && q.data.length === 0 && (
        <Card>
          <EmptyState icon={Wallet} title="Kayıtlı hesap yok" />
        </Card>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {q.data?.map((a) => (
          <Card key={a.name} className="flex flex-col">
            <div className="flex items-start justify-between gap-3 p-4">
              <div>
                <Link to={`/accounts/${encodeURIComponent(a.name)}`} className="num text-[15px] font-semibold hover:text-accent">
                  {a.name}
                </Link>
                <div className="mt-1 text-[12px] text-muted">
                  {a.profile ?? "profil yok"} {a.timeframe ? `· ${a.timeframe}` : ""}
                </div>
              </div>
              <ModeBadge mode={a.mode} />
            </div>
            <div className="px-4">
              <div className="label">Portföy değeri</div>
              <div className="num mt-1 text-[24px] font-semibold">{a.snapshot ? money(a.snapshot.equity) : "—"}</div>
              <div className="mt-1 flex gap-3 text-[12px]">
                <Pnl value={a.snapshot?.return_since_start} />
                <span className="text-muted">başlangıçtan beri</span>
              </div>
              <div className="mt-3 h-12">
                <Sparkline values={a.sparkline.map((p) => p.v)} height={48} />
              </div>
            </div>
            <div className="mt-auto flex flex-wrap gap-2 border-t border-line/60 px-4 py-3">
              <BuyPermissionBadge account={a} />
              <DriftBadge drift={a.drift} />
              {a.snapshot && <span className="num ml-auto text-[12px] text-muted">{a.snapshot.positions} pozisyon</span>}
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}

type Range = "7" | "30" | "90" | "0";

export function AccountDetailPage({ theme }: { theme: Theme }) {
  const { name = "" } = useParams();
  const [range, setRange] = useState<Range>("30");
  const q = useQuery({ queryKey: ["account", name], queryFn: () => api.get<Account>(`/accounts/${encodeURIComponent(name)}`) });
  const equity = useQuery({
    queryKey: ["equity", name, range],
    queryFn: () => api.get<EquityRow[]>(`/accounts/${encodeURIComponent(name)}/equity${qs({ days: range })}`),
  });

  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data) return <Spinner />;
  const a = q.data;
  const s = a.snapshot;

  return (
    <>
      <Link to="/accounts" className="mb-3 inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg">
        <ArrowLeft size={14} /> Hesaplar
      </Link>
      <PageHeader
        title={a.name}
        icon={Wallet}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <ModeBadge mode={a.mode} />
            {a.profile && <span>{a.profile}</span>}
            {a.timeframe && <span className="num">· {a.timeframe}</span>}
            {a.created_at && <span>· açılış {date(a.created_at)}</span>}
          </span>
        }
      />
      {a.snapshot_error && (
        <div className="mb-4">
          <Notice>{a.snapshot_error}</Notice>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Portföy değeri" icon={Wallet} value={money(s?.equity)} delta={s?.return_since_start} hint="başlangıçtan beri" />
        <KpiCard label="Nakit" icon={Banknote} value={money(s?.cash)} hint={`başlangıç ${money(a.initial_capital, 0)}`} />
        <KpiCard
          label="Pozisyonda"
          icon={PieChart}
          value={money(s?.market_value)}
          hint={`${s?.positions ?? 0} pozisyon · ${pct(s?.exposure, 1, false)} kullanım`}
        />
        <KpiCard
          label="Bugün"
          icon={Gauge}
          value={<Pnl value={a.pnl_today.total_pct} size="lg" />}
          hint={
            <span>
              gerçekleşen {pct(a.pnl_today.realized_pct)} · limit −%5
              {a.pnl_today.target_pct != null && ` · hedef +${pct(a.pnl_today.target_pct, 0, false)}`}
            </span>
          }
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Portföy değeri"
            icon={LineChart}
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
              <EquityChart theme={theme} points={equity.data.map((r) => ({ t: r.t, v: r.equity }))} baseline={a.initial_capital} height={300} />
            ) : (
              <EmptyState icon={LineChart} title="Bu aralıkta kayıt yok" />
            )}
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title="Risk durumu" icon={ShieldAlert} actions={<BuyPermissionBadge account={a} />} />
            <div className="space-y-3 p-4">
              <div>
                <Stat label="DB alım izni">{a.flags.trading_enabled ? "Açık" : "Kapalı"}</Stat>
                <Stat label="Kill-switch">{a.flags.kill_switch ? a.flags.kill_switch_reason || "Tetiklendi" : "Normal"}</Stat>
                {a.mode === "live" && (
                  <>
                    <Stat label=".env ana şalter">{a.flags.env_live_allowed ? "Açık" : "Kapalı"}</Stat>
                    <Stat label="İlk emir onayı">{a.flags.first_order_confirmed ? "Var" : "Yok"}</Stat>
                  </>
                )}
                <Stat label="Kesinleşmemiş emir">{a.unresolved_orders}</Stat>
              </div>
              {a.flags.blockers.length > 0 && (
                <div className="text-[12px] text-muted">Alımı durduran: {a.flags.blockers.join(", ")}</div>
              )}
              <RiskActions account={a} />
            </div>
          </Card>
          <Card>
            <CardHeader title="Performans izleme" icon={Activity} actions={<DriftBadge drift={a.drift} />} />
            <div className="p-4">
              <DriftDetail drift={a.drift} />
            </div>
          </Card>
          {s && (
            <Card>
              <CardHeader title="Muhasebe" icon={Banknote} />
              <div className="p-4">
                <Stat label="Nakit">{usd(s.cash)}</Stat>
                <Stat label="Açık pozisyon maliyeti">{usd(s.allocated)}</Stat>
                <Stat label="Piyasa değeri">{usd(s.market_value)}</Stat>
                <Stat label="Gerçekleşmemiş K/Z">
                  <Pnl value={s.unrealized_pnl} kind="money" />
                </Stat>
              </div>
            </Card>
          )}
        </div>
      </div>

      <Card className="mt-4">
        <CardHeader title="Açık pozisyonlar" icon={CandlestickChart} />
        <PositionsTable account={a.name} />
      </Card>
    </>
  );
}
