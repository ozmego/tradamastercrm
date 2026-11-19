import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  ArrowLeftRight,
  CandlestickChart,
  Cpu,
  Database,
  FileChartColumn,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  RadioTower,
  Receipt,
  ScrollText,
  ShieldAlert,
  Sun,
  UserRound,
  Users,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { api } from "../api/client";
import type { Overview } from "../api/types";
import { useAuth } from "../auth";
import type { Theme } from "../lib/theme";
import { StatusDot } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Portföy",
    items: [
      { to: "/", label: "Genel Bakış", icon: LayoutDashboard },
      { to: "/accounts", label: "Hesaplar", icon: Wallet },
      { to: "/positions", label: "Pozisyonlar", icon: CandlestickChart },
      { to: "/trades", label: "İşlemler", icon: ArrowLeftRight },
      { to: "/orders", label: "Emirler", icon: Receipt },
    ],
  },
  {
    section: "Raporlama",
    items: [{ to: "/reports", label: "Raporlar", icon: FileChartColumn }],
  },
  {
    section: "Kontrol",
    items: [
      { to: "/risk", label: "Risk", icon: ShieldAlert },
      { to: "/processes", label: "Süreçler", icon: Cpu },
      { to: "/collectors", label: "Toplayıcılar", icon: RadioTower },
    ],
  },
  {
    section: "Araştırma",
    items: [
      { to: "/backtests", label: "Backtest", icon: FlaskConical },
      { to: "/data", label: "Veri kaynakları", icon: Database },
    ],
  },
  {
    section: "Yönetim",
    items: [
      { to: "/audit", label: "Denetim kaydı", icon: ScrollText },
      { to: "/users", label: "Kullanıcılar", icon: Users },
    ],
  },
];

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <span className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-raised text-gold">
        <CandlestickChart size={17} strokeWidth={1.9} />
      </span>
      <div className="leading-tight">
        <div className="text-[14px] font-semibold tracking-tight">TradeCRM</div>
        <div className="text-[10.5px] uppercase tracking-[0.12em] text-muted">Admin</div>
      </div>
    </div>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="space-y-5">
      {NAV.map((group) => (
        <div key={group.section}>
          <div className="label mb-1.5 px-3">{group.section}</div>
          <ul className="space-y-0.5">
            {group.items.map(({ to, label, icon: Icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={to === "/"}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    clsx(
                      "group flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition",
                      isActive ? "bg-raised text-fg shadow-[inset_2px_0_0_rgb(var(--gold))]" : "text-muted hover:bg-raised/60 hover:text-fg",
                    )
                  }
                >
                  <Icon size={16} strokeWidth={1.8} className="shrink-0" />
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function worstCollector(c: Overview["collectors"]): string | undefined {
  const order = ["failing", "stopped", "degraded", "never", "healthy"];
  const all = Object.values(c ?? {});
  return order.find((s) => all.includes(s as never));
}

function collectorTone(c: Overview["collectors"]): string | undefined {
  const w = worstCollector(c);
  return w === "failing" ? "text-loss" : w === "degraded" || w === "stopped" ? "text-warn" : undefined;
}

function collectorText(running: boolean | undefined, c: Overview["collectors"]): string {
  const w = worstCollector(c);
  if (w === "failing") return "Başarısız";
  if (!running) return "Durdu";
  if (w === "degraded") return "Sorunlu";
  return "Çalışıyor";
}

function SystemStatus() {
  const { data } = useQuery({
    queryKey: ["overview"],
    queryFn: () => api.get<Overview>("/overview"),
    refetchInterval: 15_000,
  });
  const critical = data?.alerts.filter((a) => a.level === "critical").length ?? 0;
  return (
    <div className="card space-y-2 p-3 text-[12px]">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-muted">
          <StatusDot on={!!data?.processes.live} pulse /> Paper runner
        </span>
        <span>{data?.processes.live ? "Çalışıyor" : "Durdu"}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-muted">
          <StatusDot on={!!data?.processes.collector} pulse /> Toplayıcı
        </span>
        <span className={clsx(collectorTone(data?.collectors))}>
          {collectorText(data?.processes.collector, data?.collectors)}
        </span>
      </div>
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-muted">
          <Activity size={12} className={critical ? "text-loss" : "text-gain"} /> Uyarı
        </span>
        <span className={clsx("num", critical && "text-loss")}>{critical}</span>
      </div>
    </div>
  );
}

export function Layout({ children, theme, onToggleTheme }: { children: ReactNode; theme: Theme; onToggleTheme: () => void }) {
  const { me, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMobileOpen(false), [location.pathname]);

  const sidebar = (
    <div className="flex h-full flex-col gap-6 px-3 py-4">
      <Brand />
      <div className="flex-1 overflow-y-auto">
        <Nav onNavigate={() => setMobileOpen(false)} />
      </div>
      <SystemStatus />
    </div>
  );

  return (
    <div className="flex min-h-full">
      <aside className="no-print sticky top-0 hidden h-screen w-60 shrink-0 border-r border-line/70 bg-surface/60 lg:block">{sidebar}</aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-surface">
            <button className="absolute right-3 top-4 text-muted" onClick={() => setMobileOpen(false)} aria-label="Menüyü kapat">
              <X size={18} />
            </button>
            {sidebar}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-line/70 bg-bg/80 px-4 backdrop-blur lg:px-6">
          <div className="flex items-center gap-2">
            <button className="btn-ghost px-2 py-1.5 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Menü">
              <Menu size={17} />
            </button>
            <span className="hidden text-[12px] text-muted sm:inline">
              Paper hesaplar yönetilebilir · canlı hesapta yalnızca güvenli yön (kapatma)
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-ghost px-2 py-1.5" onClick={onToggleTheme} aria-label="Temayı değiştir" title="Tema">
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <span className="hidden items-center gap-1.5 rounded-lg border border-line bg-raised/60 px-2.5 py-1.5 text-[12.5px] sm:flex">
              <UserRound size={14} className="text-gold" />
              {me.username}
            </span>
            <button className="btn-ghost px-2 py-1.5" onClick={() => void logout()} aria-label="Çıkış" title="Çıkış">
              <LogOut size={16} />
            </button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 lg:px-6">{children}</main>
        <footer className="no-print px-6 pb-5 text-[11px] text-muted">
          Grafikler: TradingView Lightweight Charts™ (Apache-2.0) · İkonlar: Lucide (ISC)
        </footer>
      </div>
    </div>
  );
}
