import { Compass } from "lucide-react";
import { Link, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { EmptyState } from "./components/ui";
import { useTheme } from "./lib/theme";
import { AccountDetailPage, AccountsPage } from "./pages/Accounts";
import { AuditPage } from "./pages/Audit";
import { BacktestDetailPage, BacktestsPage } from "./pages/Backtests";
import { CollectorsPage } from "./pages/Collectors";
import { DataPage } from "./pages/Data";
import { OrdersPage } from "./pages/Orders";
import { OverviewPage } from "./pages/Overview";
import { PositionsPage, TradeChartPage } from "./pages/Positions";
import { ProcessesPage } from "./pages/Processes";
import { ReportDetailPage, ReportsPage } from "./pages/Reports";
import { RiskPage } from "./pages/Risk";
import { TradesPage } from "./pages/Trades";
import { UsersPage } from "./pages/Users";

export function App() {
  const { theme, toggle } = useTheme();
  return (
    <Layout theme={theme} onToggleTheme={toggle}>
      <Routes>
        <Route path="/" element={<OverviewPage theme={theme} />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/accounts/:name" element={<AccountDetailPage theme={theme} />} />
        <Route path="/positions" element={<PositionsPage />} />
        <Route path="/trades" element={<TradesPage />} />
        <Route path="/trades/:id/chart" element={<TradeChartPage theme={theme} />} />
        <Route path="/orders" element={<OrdersPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/reports/:id" element={<ReportDetailPage theme={theme} />} />
        <Route path="/risk" element={<RiskPage />} />
        <Route path="/processes" element={<ProcessesPage />} />
        <Route path="/collectors" element={<CollectorsPage />} />
        <Route path="/backtests" element={<BacktestsPage />} />
        <Route path="/backtests/:id" element={<BacktestDetailPage theme={theme} />} />
        <Route path="/data" element={<DataPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route
          path="*"
          element={
            <EmptyState icon={Compass} title="Sayfa bulunamadı">
              <Link to="/" className="text-accent">
                Genel bakışa dön
              </Link>
            </EmptyState>
          }
        />
      </Routes>
    </Layout>
  );
}
