import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, Download, LineChart, Percent, Receipt, Trophy } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, qs } from "../api/client";
import type { Account, TradesPage as TradesData } from "../api/types";
import { Badge, Card, EmptyState, ErrorState, KpiCard, PageHeader, Pagination, Pnl, Segmented, Spinner } from "../components/ui";
import { dateTime, exitReason, money, pct, price, qty } from "../lib/format";

type Status = "" | "OPEN" | "CLOSED";

export function TradesPage() {
  const [status, setStatus] = useState<Status>("");
  const [account, setAccount] = useState("");
  const [symbol, setSymbol] = useState("");
  const [page, setPage] = useState(1);
  const size = 50;
  const filters = { status, account, symbol: symbol.trim().toUpperCase() };

  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/accounts") });
  const q = useQuery({
    queryKey: ["trades", filters, page],
    queryFn: () => api.get<TradesData>(`/trades${qs({ ...filters, page, size })}`),
    placeholderData: keepPreviousData,
  });
  const s = q.data?.summary;

  const reset = (fn: () => void) => {
    fn();
    setPage(1);
  };

  return (
    <>
      <PageHeader
        title="İşlemler"
        icon={ArrowLeftRight}
        description="Bütün alım-satim kayıtları; komisyonlar giriş + çıkış birlikte"
        actions={
          <a className="btn-ghost" href={`/api/trades.csv${qs(filters)}`}>
            <Download size={14} /> CSV
          </a>
        }
      />

      <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Kapanan işlem" icon={Receipt} value={s ? s.closed : "—"} />
        <KpiCard label="Kazanma oranı" icon={Trophy} value={s && s.closed ? pct(s.wins / s.closed, 1, false) : "—"} hint={s ? `${s.wins} kazanan` : undefined} />
        <KpiCard label="Net K/Z" icon={ArrowLeftRight} value={<Pnl value={s?.net_pnl} kind="money" size="lg" />} />
        <KpiCard label="Ödenen komisyon" icon={Percent} value={money(s?.fees)} />
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-line/60 px-4 py-3">
          <Segmented<Status>
            value={status}
            onChange={(v) => reset(() => setStatus(v))}
            options={[
              { value: "", label: "Tümü" },
              { value: "OPEN", label: "Açık" },
              { value: "CLOSED", label: "Kapalı" },
            ]}
          />
          <select className="input w-auto" value={account} onChange={(e) => reset(() => setAccount(e.target.value))} aria-label="Hesap">
            <option value="">Bütün hesaplar</option>
            {accounts.data?.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
          <input className="input w-36" placeholder="Sembol (BTC/USDT)" value={symbol} onChange={(e) => reset(() => setSymbol(e.target.value))} />
        </div>
        {q.error && <ErrorState error={q.error} />}
        {!q.data && !q.error && <Spinner />}
        {q.data && q.data.items.length === 0 && <EmptyState icon={ArrowLeftRight} title="Kayıt yok" />}
        {q.data && q.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Sembol</th>
                    <th>Hesap</th>
                    <th>Durum</th>
                    <th>Giriş</th>
                    <th className="text-right">Giriş fiyatı</th>
                    <th className="text-right">Çıkış fiyatı</th>
                    <th className="text-right">Miktar</th>
                    <th className="text-right">Getiri</th>
                    <th className="text-right">Net K/Z</th>
                    <th className="text-right">Komisyon</th>
                    <th>Çıkış nedeni</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((t) => (
                    <tr key={t.id}>
                      <td className="font-medium">{t.symbol}</td>
                      <td className="num text-muted">{t.account}</td>
                      <td>{t.status === "OPEN" ? <Badge tone="accent">Açık</Badge> : <Badge>Kapalı</Badge>}</td>
                      <td className="text-muted">{dateTime(t.entry_time)}</td>
                      <td className="num text-right">{price(t.entry_price)}</td>
                      <td className="num text-right">{price(t.exit_price)}</td>
                      <td className="num text-right">{qty(t.qty)}</td>
                      <td className="text-right">
                        <Pnl value={t.return_pct} />
                      </td>
                      <td className="text-right">
                        <Pnl value={t.realized_pnl} kind="money" />
                      </td>
                      <td className="num text-right text-muted">{money(t.fees)}</td>
                      <td className="text-muted">{exitReason(t.exit_reason)}</td>
                      <td className="text-right">
                        <Link to={`/trades/${t.id}/chart`} className="btn-ghost px-2 py-1" title="Grafik">
                          <LineChart size={14} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} size={size} total={q.data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </>
  );
}
