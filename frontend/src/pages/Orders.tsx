import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fingerprint, Receipt } from "lucide-react";
import { useState } from "react";

import { api, qs } from "../api/client";
import type { OrdersPage as OrdersData } from "../api/types";
import { OrderStatusBadge, SideBadge } from "../components/domain";
import { Card, EmptyState, ErrorState, Notice, PageHeader, Pagination, Spinner } from "../components/ui";
import { dateTime, money, price, qty } from "../lib/format";

const STATUSES = ["", "FILLED", "UNKNOWN", "ABANDONED", "REJECTED", "NEW"] as const;
const LABEL: Record<string, string> = {
  "": "Tümü",
  FILLED: "Doldu",
  UNKNOWN: "Belirsiz",
  ABANDONED: "Terk edildi",
  REJECTED: "Reddedildi",
  NEW: "Yeni",
};

export function OrdersPage() {
  const [status, setStatus] = useState<string>("");
  const [page, setPage] = useState(1);
  const size = 50;
  const q = useQuery({
    queryKey: ["orders", status, page],
    queryFn: () => api.get<OrdersData>(`/orders${qs({ status, page, size })}`),
    placeholderData: keepPreviousData,
  });
  const counts = q.data?.status_counts ?? {};
  const unresolved = (counts.UNKNOWN ?? 0) + (counts.NEW ?? 0);

  return (
    <>
      <PageHeader
        title="Emirler"
        icon={Receipt}
        description="Her emir (hesap, sembol, yön, mum) ile tekil; zaman aşımında borsaya aynı kimlikle sorulur"
      />
      {unresolved > 0 && (
        <div className="mb-4">
          <Notice tone="loss">
            {unresolved} kesinleşmemiş emir var. Uzlaştırılana kadar ilgili hesapta yeni alım yapılmaz; runner her tick'te borsaya sorar.
          </Notice>
        </div>
      )}
      <Card>
        <div className="flex flex-wrap gap-1.5 border-b border-line/60 px-4 py-3">
          {STATUSES.map((s) => (
            <button
              key={s}
              onClick={() => {
                setStatus(s);
                setPage(1);
              }}
              className={
                status === s
                  ? "rounded-lg bg-raised px-2.5 py-1 text-[12px] font-medium text-fg ring-1 ring-line"
                  : "rounded-lg px-2.5 py-1 text-[12px] text-muted hover:text-fg"
              }
            >
              {LABEL[s]}
              {s && counts[s] ? <span className="num ml-1.5 text-muted">{counts[s]}</span> : null}
            </button>
          ))}
        </div>
        {q.error && <ErrorState error={q.error} />}
        {!q.data && !q.error && <Spinner />}
        {q.data && q.data.items.length === 0 && <EmptyState icon={Receipt} title="Emir yok" />}
        {q.data && q.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Zaman</th>
                    <th>Hesap</th>
                    <th>Sembol</th>
                    <th>Yön</th>
                    <th>Durum</th>
                    <th className="text-right">Tutar</th>
                    <th className="text-right">Dolum</th>
                    <th className="text-right">Ort. fiyat</th>
                    <th className="text-right">Deneme</th>
                    <th>Neden / hata</th>
                    <th>Kimlik</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((o) => (
                    <tr key={o.id}>
                      <td className="text-muted">{dateTime(o.created_at)}</td>
                      <td className="num text-muted">{o.account}</td>
                      <td className="font-medium">{o.symbol}</td>
                      <td>
                        <SideBadge side={o.side} />
                      </td>
                      <td>
                        <OrderStatusBadge status={o.status} />
                      </td>
                      <td className="num text-right">{money(o.requested_notional)}</td>
                      <td className="num text-right">{qty(o.filled_qty)}</td>
                      <td className="num text-right">{price(o.avg_price)}</td>
                      <td className="num text-right">{o.attempts}</td>
                      <td className="max-w-[260px] truncate text-muted" title={o.error ?? o.reason ?? ""}>
                        {o.error ?? o.reason ?? "—"}
                      </td>
                      <td>
                        <span className="num inline-flex items-center gap-1 text-[11.5px] text-muted" title={o.client_order_id}>
                          <Fingerprint size={12} />
                          {o.client_order_id.slice(0, 11)}…
                        </span>
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
