import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, Cpu, DatabaseZap, History, Landmark, LogIn, Play, RotateCcw, ServerCog, ShieldCheck, Square, Terminal } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { AutostartInfo, AutostartKey, ExchangeRunner, Processes, ProfileInfo } from "../api/types";
import { Card, CardHeader, ErrorState, Field, Notice, PageHeader, Spinner, Stat, StatusDot } from "../components/ui";
import { dateTime, relative } from "../lib/format";

// Aktif karar (2026-11-18): piyasa degerine gore en buyuk 5 coin.
const DEFAULT_SYMBOLS = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT";

interface LiveMeta {
  profile?: string;
  symbols?: string[];
  timeframe?: string;
  initial_capital?: number;
  started_by?: string;
}

function LogView({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; // en yeni satirlar gorunsun
  }, [lines]);
  return (
    <pre ref={ref} className="num max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg/80 p-3 text-[11.5px] leading-relaxed text-muted">
      {lines.length ? lines.join("\n") : "Log boş"}
    </pre>
  );
}

function ProcessHeader({ running }: { running: boolean }) {
  return (
    <div className="flex items-center gap-2 text-[12.5px]">
      <StatusDot on={running} pulse />
      <span className={running ? "text-gain" : "text-muted"}>{running ? "Çalışıyor" : "Durdu"}</span>
    </div>
  );
}

function Toggle({ on, disabled, onChange, label }: { on: boolean; disabled?: boolean; onChange: () => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={clsx(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition disabled:opacity-50",
        on ? "border-gain/50 bg-gain/30" : "border-line bg-raised",
      )}
    >
      <span
        className={clsx("inline-block h-3.5 w-3.5 rounded-full shadow transition", on ? "translate-x-[18px] bg-gain" : "translate-x-[3px] bg-muted")}
      />
    </button>
  );
}

const AUTOSTART_HINT: Record<AutostartKey, string> = {
  collector: "Windows oturumu açıldıktan 1 dk sonra toplayıcıyı başlatır.",
  live: "Oturum açıldıktan 2 dk sonra paper runner'ı son başlatıldığı parametrelerle yeniden başlatır.",
  admin: "Oturum açıldıktan 1 dk sonra bu paneli başlatır (bilgisayar yeniden başlayınca da erişilebilir olur).",
  live_kucoin: "Oturum açıldıktan 3 dk sonra KuCoin runner'ı son parametreleriyle başlatır. Emir şalterlerini AÇMAZ.",
};

/** Gercek hesap runner'i: panel yalnizca durdurur ve SON parametrelerle yeniden baslatir. */
function ExchangeRunnerCard({ id, r, auto, autoLoading }: { id: string; r: ExchangeRunner; auto: AutostartInfo | undefined; autoLoading: boolean }) {
  const qc = useQueryClient();
  const act = useMutation({
    mutationFn: (action: "start" | "stop") => api.post(`/processes/exchange/${id}/${action}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["processes"] }),
  });
  const meta = (r.running ? r.meta : r.last?.meta) as LiveMeta | null;
  const title = id === "kucoin" ? "KuCoin runner" : `${id} runner`;
  return (
    <Card>
      <CardHeader title={title} icon={Landmark} subtitle="GERÇEK HESAP · parametreler komut satırından verilir" actions={<ProcessHeader running={r.running} />} />
      <div className="space-y-4 p-4">
        <div
          className={clsx(
            "flex items-start gap-2 rounded-lg border p-3 text-[12px]",
            r.env_orders_open ? "border-loss/40 bg-loss/10 text-loss" : "border-gain/30 bg-gain/5 text-gain",
          )}
        >
          <ShieldCheck size={15} className="mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">{r.env_orders_open ? "Emir şalteri AÇIK — gerçek emir gidebilir" : "Emir şalteri kapalı — yalnızca izler, emir göndermez"}</div>
            <div className="text-muted">
              Şalter <code className="num">.env</code> dosyasında (TRADING_ENABLED / DRY_RUN); panelden değiştirilemez. Ayrıca alım izni ve ilk emir onayı gerekir.
            </div>
          </div>
        </div>
        {meta ? (
          <div>
            <Stat label="Profil">{meta.profile ?? "—"}</Stat>
            <Stat label="Zaman dilimi">{meta.timeframe ?? "—"}</Stat>
            <Stat label="Bütçe">{meta.initial_capital?.toLocaleString("tr-TR") ?? "—"} USDT</Stat>
            {r.running && (
              <Stat label="Başlangıç">
                {dateTime(r.started_at)} <span className="text-muted">({relative(r.started_at)})</span>
              </Stat>
            )}
            <SymbolChips symbols={meta.symbols} />
          </div>
        ) : (
          <div className="text-[12px] text-muted">
            Henüz başlatılmadı. İlk kez komut satırından başlatın:{" "}
            <code className="num rounded bg-raised px-1">tradecrm live start --mode {id} --initial-capital …</code>
          </div>
        )}
        <div className="flex gap-2">
          <button className="btn-success" disabled={r.running || !r.last || act.isPending} onClick={() => act.mutate("start")}>
            <Play size={14} /> Son parametrelerle başlat
          </button>
          <button className="btn-ghost" disabled={!r.running || act.isPending} onClick={() => act.mutate("stop")}>
            <Square size={14} /> Durdur
          </button>
        </div>
        {act.error && <Notice tone="loss">{(act.error as Error).message}</Notice>}
        {id === "kucoin" && (
          <AutostartRow k="live_kucoin" info={auto} loading={autoLoading} blockedReason={r.last ? undefined : "Önce komut satırından bir kez başlatın."} />
        )}
        <div>
          <div className="label mb-1.5 flex items-center gap-1.5">
            <Terminal size={12} /> Son log
          </div>
          <LogView lines={r.log} />
        </div>
      </div>
    </Card>
  );
}

function AutostartRow({
  k,
  info,
  loading,
  blockedReason,
}: {
  k: AutostartKey;
  info: AutostartInfo | undefined;
  loading: boolean;
  blockedReason?: string;
}) {
  const qc = useQueryClient();
  const task = info?.tasks[k];
  const on = !!task?.installed;
  const toggle = useMutation({
    mutationFn: () => api.post(`/autostart/${k}/${on ? "disable" : "enable"}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["autostart"] }),
  });
  const unsupported = info && !info.supported;
  const disabled = loading || toggle.isPending || unsupported || !!info?.error || (!on && !!blockedReason);

  return (
    <div className="rounded-lg border border-line bg-bg/40 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
            <LogIn size={13} className="text-muted" /> Oturum açılışında başlat
          </div>
          <div className="mt-0.5 text-[11.5px] text-muted">
            {unsupported ? "Yalnızca Windows'ta (Görev Zamanlayıcı) destekleniyor." : AUTOSTART_HINT[k]}
          </div>
          {on && task && (
            <div className="mt-1 text-[11.5px] text-muted">
              Görev: <span className="text-fg">{task.task_name}</span>
              {task.last_run ? (
                <>
                  {" "}· son çalışma <span className="num">{task.last_run}</span>{" "}
                  {task.last_result === 0 ? (
                    <span className="text-gain">(başarılı)</span>
                  ) : (
                    <span className="text-loss">(hata kodu {task.last_result})</span>
                  )}
                </>
              ) : (
                " · henüz çalışmadı"
              )}
            </div>
          )}
          {!on && blockedReason && <div className="mt-1 text-[11.5px] text-warn">{blockedReason}</div>}
        </div>
        <Toggle on={on} disabled={disabled} onChange={() => toggle.mutate()} label="Oturum açılışında başlat" />
      </div>
      {info?.error && <div className="mt-2 text-[11.5px] text-loss">Görev Zamanlayıcı okunamadı: {info.error}</div>}
      {toggle.error && <div className="mt-2 text-[11.5px] text-loss">{(toggle.error as Error).message}</div>}
    </div>
  );
}

function SymbolChips({ symbols }: { symbols?: string[] }) {
  if (!symbols?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {symbols.map((s) => (
        <span key={s} className="num rounded-md bg-raised px-1.5 py-0.5 text-[11px] text-muted">
          {s}
        </span>
      ))}
    </div>
  );
}

export function ProcessesPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["processes"], queryFn: () => api.get<Processes>("/processes"), refetchInterval: 5_000 });
  const auto = useQuery({ queryKey: ["autostart"], queryFn: () => api.get<AutostartInfo>("/autostart"), refetchInterval: 30_000 });
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => api.get<ProfileInfo[]>("/profiles"), refetchInterval: false });

  const [profile, setProfile] = useState("donchian_fng");
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [timeframe, setTimeframe] = useState("1d");
  const [capital, setCapital] = useState("10000");

  const act = useMutation({
    mutationFn: (args: { path: string; body?: unknown }) => api.post(args.path, args.body),
    onSuccess: () => void qc.invalidateQueries(),
  });

  const startLive = (e: FormEvent) => {
    e.preventDefault();
    act.mutate({
      path: "/processes/live/start",
      body: {
        profile,
        symbols: symbols.split(",").map((s) => s.trim()).filter(Boolean),
        timeframe,
        initial_capital: Number(capital),
      },
    });
  };

  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data) return <Spinner />;
  const { live, collector, admin, live_last } = q.data;
  const meta = live.meta as LiveMeta | null;
  const last = (live_last?.meta ?? null) as LiveMeta | null;

  const loadLast = () => {
    if (!last) return;
    if (last.profile) setProfile(last.profile);
    if (last.symbols) setSymbols(last.symbols.join(","));
    if (last.timeframe) setTimeframe(last.timeframe);
    if (last.initial_capital) setCapital(String(last.initial_capital));
  };

  return (
    <>
      <PageHeader
        title="Süreçler"
        icon={Cpu}
        description="Arka plan süreçlerini başlatın, durdurun ve oturum açılışında otomatik başlamalarını ayarlayın"
      />
      {act.error && (
        <div className="mb-4">
          <Notice tone="loss">{(act.error as Error).message}</Notice>
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Paper runner" icon={Bot} subtitle="Strateji -> risk -> paper broker; mum kapanışına hizalı" actions={<ProcessHeader running={live.running} />} />
          <div className="space-y-4 p-4">
            {live.running ? (
              <>
                <div>
                  <Stat label="Profil">{meta?.profile ?? "—"}</Stat>
                  <Stat label="Zaman dilimi">{meta?.timeframe ?? "—"}</Stat>
                  <Stat label="Sermaye">{meta?.initial_capital?.toLocaleString("tr-TR") ?? "—"} USDT</Stat>
                  <Stat label="Başlatan">{meta?.started_by === "autostart" ? "Otomatik başlatma" : (meta?.started_by ?? "—")}</Stat>
                  <Stat label="Başlangıç">
                    {dateTime(live.started_at)} <span className="text-muted">({relative(live.started_at)})</span>
                  </Stat>
                  <SymbolChips symbols={meta?.symbols} />
                </div>
                <button className="btn-danger" disabled={act.isPending} onClick={() => act.mutate({ path: "/processes/live/stop" })}>
                  <Square size={14} /> Durdur
                </button>
              </>
            ) : (
              <>
                {last && (
                  <div className="rounded-lg border border-line bg-bg/40 p-3 text-[12px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 font-medium">
                        <History size={13} className="text-muted" /> Son başlatma
                      </span>
                      <button type="button" className="btn-ghost px-2 py-1 text-[12px]" onClick={loadLast}>
                        <RotateCcw size={13} /> Forma yükle
                      </button>
                    </div>
                    <div className="mt-1 text-muted">
                      {last.profile} · {last.timeframe} · {last.initial_capital?.toLocaleString("tr-TR")} USDT ·{" "}
                      {relative(live_last?.started_at)}
                    </div>
                    <SymbolChips symbols={last.symbols} />
                  </div>
                )}
                <form onSubmit={startLive} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Profil" hint={profiles.data?.find((p) => p.name === profile)?.description}>
                    <select className="input" value={profile} onChange={(e) => setProfile(e.target.value)}>
                      {profiles.data?.map((p) => (
                        <option key={p.name} value={p.name}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Zaman dilimi">
                    <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                      {["15m", "1h", "4h", "1d"].map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                  </Field>
                  <div className="sm:col-span-2">
                    <Field label="Semboller" hint="Virgülle ayrılmış; aynı hesapta nakit ve toplam pozisyon tavanı (profilde, şu an %35) paylaşılır">
                      <textarea className="input num min-h-[64px]" value={symbols} onChange={(e) => setSymbols(e.target.value)} />
                    </Field>
                  </div>
                  <Field label="Başlangıç sermayesi (USDT)" hint="Kayıtlı hesap farklı sermayeyle başlatılamaz">
                    <input className="input num" type="number" min={100} step={100} value={capital} onChange={(e) => setCapital(e.target.value)} />
                  </Field>
                  <div className="flex items-end">
                    <button className="btn-success w-full" disabled={act.isPending}>
                      <Play size={14} /> Paper runner'ı başlat
                    </button>
                  </div>
                  <div className="sm:col-span-2">
                    <Notice tone="accent">
                      Yalnızca paper. Alım için hesabın alım izni de açık olmalı (
                      <Link to="/risk" className="underline">
                        Risk sayfası
                      </Link>
                      ).
                    </Notice>
                  </div>
                </form>
              </>
            )}
            <AutostartRow
              k="live"
              info={auto.data}
              loading={auto.isLoading}
              blockedReason={last ? undefined : "Önce runner'ı bir kez başlatın: otomatik başlatma son parametreleri kullanır."}
            />
            <div>
              <div className="label mb-1.5 flex items-center gap-1.5">
                <Terminal size={12} /> Son log
              </div>
              <LogView lines={live.log} />
            </div>
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Veri toplayıcı"
              icon={DatabaseZap}
              subtitle="Open interest + long/short oranı (Binance ~30 gün saklıyor)"
              actions={<ProcessHeader running={collector.running} />}
            />
            <div className="space-y-4 p-4">
              <div>
                <Stat label="Başlangıç">
                  {collector.running ? (
                    <>
                      {dateTime(collector.started_at)} <span className="text-muted">({relative(collector.started_at)})</span>
                    </>
                  ) : (
                    "—"
                  )}
                </Stat>
                <Stat label="Sağlık ve çalışma geçmişi">
                  <Link to="/collectors" className="text-accent">
                    Toplayıcılar sayfası →
                  </Link>
                </Stat>
              </div>
              <div className="flex gap-2">
                <button className="btn-success" disabled={collector.running || act.isPending} onClick={() => act.mutate({ path: "/processes/collector/start" })}>
                  <Play size={14} /> Başlat
                </button>
                <button className="btn-ghost" disabled={!collector.running || act.isPending} onClick={() => act.mutate({ path: "/processes/collector/stop" })}>
                  <Square size={14} /> Durdur
                </button>
              </div>
              <AutostartRow k="collector" info={auto.data} loading={auto.isLoading} />
              <div>
                <div className="label mb-1.5 flex items-center gap-1.5">
                  <Terminal size={12} /> Son log
                </div>
                <LogView lines={collector.log} />
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader title="Admin paneli" icon={ServerCog} subtitle="Bu arayüzü sunan süreç" actions={<ProcessHeader running={admin.running} />} />
            <div className="space-y-4 p-4">
              <div>
                <Stat label="PID">{admin.pid}</Stat>
                <Stat label="Başlangıç">
                  {admin.started_at ? (
                    <>
                      {dateTime(admin.started_at)} <span className="text-muted">({relative(admin.started_at)})</span>
                    </>
                  ) : (
                    "—"
                  )}
                </Stat>
              </div>
              <div className="text-[11.5px] text-muted">
                Arayüz bu süreç üzerinde çalıştığı için buradan durdurulamaz. Kapatmak için: terminalde açtıysanız Ctrl+C, arka planda (otomatik başlatma) açıldıysa{" "}
                <code className="num rounded bg-raised px-1">tradecrm admin stop</code>.
              </div>
              <AutostartRow k="admin" info={auto.data} loading={auto.isLoading} />
            </div>
          </Card>
        </div>
      </div>
      {q.data.exchange_runners && Object.keys(q.data.exchange_runners).length > 0 && (
        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
          {Object.entries(q.data.exchange_runners).map(([id, r]) => (
            <ExchangeRunnerCard key={id} id={id} r={r} auto={auto.data} autoLoading={auto.isLoading} />
          ))}
        </div>
      )}
    </>
  );
}
