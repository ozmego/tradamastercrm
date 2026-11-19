import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Lock, OctagonX, Play, RotateCcw, Square } from "lucide-react";
import { useState } from "react";

import { api } from "../api/client";
import type { Account } from "../api/types";
import { ConfirmDialog } from "./ConfirmDialog";
import { Notice } from "./ui";

type Action = "enable" | "disable" | "trip" | "reset";

const COPY: Record<Action, { title: string; label: string; danger: boolean; text: (a: string) => string }> = {
  enable: {
    title: "Alım iznini aç",
    label: "Alımı aç",
    danger: false,
    text: (a) => `${a} hesabında yeni alımlara izin verilecek. Risk kontrolleri (tavan, günlük zarar, devre kesiciler) çalışmaya devam eder.`,
  },
  disable: {
    title: "Alım iznini kapat",
    label: "Alımı kapat",
    danger: true,
    text: (a) => `${a} hesabında yeni alım yapılmayacak. Açık pozisyonların çıkışları (stop, kâr al, sinyal) çalışmaya devam eder.`,
  },
  trip: {
    title: "Kill-switch'i tetikle",
    label: "Kill-switch",
    danger: true,
    text: (a) => `${a} hesabında bütün yeni alımlar anında durur; sıfırlanana kadar kalır. Çıkışlar açık kalır.`,
  },
  reset: {
    title: "Kill-switch'i sıfırla",
    label: "Sıfırla",
    danger: false,
    text: (a) => `${a} hesabının kill-switch'i sıfırlanır; devre kesici sayaçları da bu andan başlar. Alım izni ayrıca açık olmalı.`,
  },
};

export function RiskActions({ account, compact = false }: { account: Account; compact?: boolean }) {
  const qc = useQueryClient();
  const [action, setAction] = useState<Action | null>(null);
  const [reason, setReason] = useState("");
  const live = account.mode === "live";
  const f = account.flags;

  const mutation = useMutation({
    mutationFn: async (a: Action) => {
      const name = encodeURIComponent(account.name);
      const confirm = account.name;
      if (a === "enable" || a === "disable") return api.post(`/risk/${name}/trading`, { enabled: a === "enable", confirm });
      if (a === "trip") return api.post(`/risk/${name}/kill-switch/trip`, { confirm, reason: reason || "manuel" });
      return api.post(`/risk/${name}/kill-switch/reset`, { confirm });
    },
    onSuccess: () => {
      setAction(null);
      void qc.invalidateQueries();
    },
  });

  const open = (a: Action) => {
    mutation.reset();
    setReason("");
    setAction(a);
  };

  return (
    <div className="space-y-3">
      <div className={compact ? "flex flex-wrap gap-2" : "grid grid-cols-2 gap-2"}>
        {f.trading_enabled ? (
          <button className="btn-ghost" onClick={() => open("disable")}>
            <Square size={14} className="text-loss" /> Alımı kapat
          </button>
        ) : (
          <button className="btn-ghost" onClick={() => open("enable")} disabled={live} title={live ? "Canlı hesapta yalnızca CLI" : undefined}>
            {live ? <Lock size={14} /> : <Play size={14} className="text-gain" />} Alımı aç
          </button>
        )}
        {f.kill_switch ? (
          <button className="btn-ghost" onClick={() => open("reset")} disabled={live} title={live ? "Canlı hesapta yalnızca CLI" : undefined}>
            {live ? <Lock size={14} /> : <RotateCcw size={14} className="text-accent" />} Kill-switch sıfırla
          </button>
        ) : (
          <button className="btn-ghost" onClick={() => open("trip")}>
            <OctagonX size={14} className="text-loss" /> Kill-switch
          </button>
        )}
      </div>
      {live && !compact && (
        <Notice tone="accent" icon={Lock}>
          Canlı hesapta panel yalnızca güvenli yönde çalışır (kapatma, kill-switch). İzni açmak ve ilk emir onayı:{" "}
          <span className="num">tradecrm risk ... --account {account.name}</span>
        </Notice>
      )}

      {action && (
        <ConfirmDialog
          open
          title={COPY[action].title}
          description={COPY[action].text(account.name)}
          confirmText={account.name}
          actionLabel={COPY[action].label}
          danger={COPY[action].danger}
          busy={mutation.isPending}
          error={mutation.error ? (mutation.error as Error).message : null}
          extra={
            action === "trip" ? (
              <label className="block">
                <span className="label mb-1.5 block">Neden</span>
                <input className="input" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="örneğin: borsa bakımı" />
              </label>
            ) : undefined
          }
          onConfirm={() => mutation.mutate(action)}
          onClose={() => setAction(null)}
        />
      )}
    </div>
  );
}
