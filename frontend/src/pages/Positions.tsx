import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CandlestickChart, LineChart } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api, qs } from "../api/client";
import type { Position, TradeChart } from "../api/types";
import { CandleChart } from "../components/charts";
import { Card, CardHeader, EmptyState, ErrorState, PageHeader, Pnl, Segmented, Spinner, Stat } from "../components/ui";
import { dateTime, money, pct, price, qty } from "../lib/format";
import type { Theme } from "../lib/theme";

export function PositionsTable({ account }: { account?: string }) {
  const q = useQuery({
    queryKey: ["positions", account],
    queryFn: () => api.get<Position[]>(`/positions${qs({ account })}`),
  });
  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data) return <Spinner />;
  if (q.data.length === 0) return <EmptyState icon={CandlestickChart} title="Açık pozisyon yok" />;
  return (
    <div className="overflow-x-auto">
      <table className="table-base">
        <thead>
          <tr>
            <th>Sembol</th>
            {!account && <th>Hesap</th>}
            <th className="text-right">Giriş</th>
            <th className="text-right">Son fiyat</th>
            <th className="text-right">Miktar</th>
            <th className="text-right">Değer</th>
            <th className="text-right">K/Z</th>
            <th className="text-right">Stop</th>
            <th className="text-right">Stop'a uzaklık</th>
            <th>Açılış</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {q.data.map((p) => (
            <tr key={p.id}>
              <td className="font-medium">{p.symbol}</td>
              {!account && <td className="num text-muted">{p.account}</td>}
              <td className="num text-right">{price(p.entry_price)}</td>
              <td className="num text-right">{price(p.price)}</td>
              <td className="num text-right">{qty(p.qty)}</td>
              <td className="num text-right">{money(p.market_value)}</td>
              <td className="text-right">
                <div className="flex flex-col items-end">
                  <Pnl value={p.unrealized_pnl} kind="money" />
                  <span className="text-[11.5px]">
                    <Pnl value={p.unrealized_pct} />
                  </span>
                </div>
              </td>
              <td className="num text-right">{price(p.stop)}</td>
              <td className="num text-right text-muted">{pct(p.stop_distance_pct)}</td>
              <td className="text-muted">{dateTime(p.entry_time)}</td>
              <td className="text-right">
                <Link to={`/trades/${p.id}/chart`} className="btn-ghost px-2 py-1 text-[12px]" title="Grafik">
                  <LineChart size={14} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PositionsPage() {
  return (
    <>
      <PageHeader title="Pozisyonlar" icon={CandlestickChart} description="Bütün hesaplardaki açık pozisyonlar, anlık fiyatla" />
      <Card>
        <PositionsTable />
      </Card>
    </>
  );
}

const TFS = ["1h", "4h", "1d"] as const;
type Tf = (typeof TFS)[number];

export function TradeChartPage({ theme }: { theme: Theme }) {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tf = params.get("tf") as Tf | null;
  const q = useQuery({
    queryKey: ["chart", id, tf],
    queryFn: () => api.get<TradeChart>(`/trades/${id}/chart${qs({ timeframe: tf })}`),
    refetchInterval: 60_000,
  });
  return (
    <>
      <Link to="/positions" className="mb-3 inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg">
        <ArrowLeft size={14} /> Pozisyonlar
      </Link>
      <PageHeader title={q.data ? `${q.data.symbol} #${id}` : `İşlem #${id}`} icon={LineChart} description="Giriş, stop ve kâr al seviyeleri" />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <Card className="xl:col-span-3">
          <CardHeader
            title="Fiyat"
            icon={CandlestickChart}
            subtitle={q.data ? `${q.data.timeframe} mumlar` : undefined}
            actions={
              <Segmented<Tf>
                value={(tf ?? q.data?.timeframe ?? "1h") as Tf}
                onChange={(v) => setParams({ tf: v })}
                options={TFS.map((v) => ({ value: v, label: v }))}
              />
            }
          />
          <div className="p-3">
            {q.error && <ErrorState error={q.error} />}
            {!q.data && !q.error && <Spinner />}
            {q.data && <CandleChart theme={theme} candles={q.data.candles} entry={q.data.entry} exit={q.data.exit} levels={q.data.levels} />}
          </div>
        </Card>
        {q.data && (
          <Card>
            <CardHeader title="Seviyeler" icon={LineChart} />
            <div className="p-4">
              <Stat label="Giriş">{price(q.data.entry.price)}</Stat>
              {q.data.exit && <Stat label="Çıkış">{price(q.data.exit.price)}</Stat>}
              <Stat label="Stop">{price(q.data.levels.stop)}</Stat>
              <Stat label="İlk stop">{price(q.data.levels.initial_stop)}</Stat>
              <Stat label="Kâr al">{price(q.data.levels.take_profit)}</Stat>
              {q.data.levels.stop && (
                <Stat label="Risk (girişten)">{pct(q.data.levels.stop / q.data.entry.price - 1)}</Stat>
              )}
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
