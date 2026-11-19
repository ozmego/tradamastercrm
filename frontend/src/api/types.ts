// Backend yanit tipleri (src/tradecrm/api/routers/*).

export interface Me {
  username: string;
  csrf_token: string;
  expires_at?: string;
}

export interface Point {
  t: string;
  v: number | null;
}

export interface AccountFlags {
  trading_enabled: boolean;
  kill_switch: boolean;
  kill_switch_reason: string;
  env_live_allowed: boolean | null;
  first_order_confirmed: boolean | null;
  buy_allowed: boolean;
  blockers: string[];
}

export interface Snapshot {
  cash: number;
  allocated: number;
  market_value: number;
  equity: number;
  realized_equity: number;
  unrealized_pnl: number;
  exposure: number | null;
  positions: number;
  return_since_start: number | null;
}

export interface Drift {
  status: "no_baseline" | "insufficient" | "ok" | "degraded";
  n: number;
  live_mean: number | null;
  lower_bound: number | null;
  baseline_mean: number | null;
  text: string;
}

export interface Account {
  name: string;
  mode: "paper" | "live";
  registered: boolean;
  profile: string | null;
  timeframe: string | null;
  initial_capital: number | null;
  created_at: string | null;
  flags: AccountFlags;
  snapshot: Snapshot | null;
  snapshot_error: string | null;
  pnl_today: { realized_pct: number; unrealized_pct: number | null; total_pct: number; target_pct: number | null };
  unresolved_orders: number;
  drift: Drift | null;
  sparkline: Point[];
}

export interface Alert {
  level: "critical" | "warning";
  account: string | null;
  text: string;
  source?: string;
}

export interface Overview {
  totals: {
    equity: number;
    cash: number;
    market_value: number;
    unrealized_pnl: number;
    positions: number;
    initial_capital: number;
    exposure: number | null;
    exposure_cap: number | null;
    return_since_start: number | null;
  };
  accounts: Account[];
  alerts: Alert[];
  processes: { collector: boolean; live: boolean };
  collectors?: Record<string, CollectorStatus>;
  generated_at: string;
}

export interface EquityRow {
  t: string;
  equity: number;
  cash: number;
  market_value: number;
}

export interface Position {
  id: number;
  account: string;
  symbol: string;
  strategy: string;
  entry_time: string;
  entry_price: number;
  qty: number;
  cost: number;
  price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  stop: number | null;
  take_profit: number | null;
  initial_stop: number | null;
  highest: number | null;
  stop_distance_pct: number | null;
}

export interface Trade {
  id: number;
  account: string;
  symbol: string;
  strategy: string;
  status: "OPEN" | "CLOSED";
  entry_time: string;
  entry_price: number;
  qty: number;
  exit_time: string | null;
  exit_price: number | null;
  return_pct: number | null;
  realized_pnl: number | null;
  realized_pnl_pct: number | null;
  fees: number;
  exit_reason: string | null;
  signal_reason: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

export interface TradesPage extends Paged<Trade> {
  summary: { closed: number; wins: number; net_pnl: number; fees: number };
}

export interface Order {
  id: number;
  client_order_id: string;
  account: string;
  symbol: string;
  side: "BUY" | "SELL";
  bar_time: string;
  status: string;
  reason: string | null;
  requested_notional: number | null;
  filled_qty: number | null;
  avg_price: number | null;
  trade_id: number | null;
  error: string | null;
  attempts: number;
  created_at: string;
  updated_at: string;
}

export interface OrdersPage extends Paged<Order> {
  status_counts: Record<string, number>;
}

export interface RiskEvent {
  id: number;
  account: string;
  event_type: string;
  timestamp: string;
  reason: string | null;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TradeChart {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  entry: { time: number; price: number };
  exit: { time: number; price: number } | null;
  levels: { stop: number | null; take_profit: number | null; initial_stop: number | null };
}

export interface ProcessInfo {
  running: boolean;
  pid: number | null;
  started_at: string | null;
  log_path: string;
  log: string[];
  meta: Record<string, unknown> | null;
}

export interface Processes {
  collector: ProcessInfo;
  live: ProcessInfo;
  live_last: { started_at: string; meta: Record<string, unknown> | null } | null;
  admin: { running: boolean; pid: number; started_at: string | null };
  exchange_runners?: Record<string, ExchangeRunner>;
}

export interface ExchangeRunner extends ProcessInfo {
  last: { started_at: string; meta: Record<string, unknown> | null } | null;
  env_orders_open: boolean;
}

export type AutostartKey = "collector" | "live" | "admin" | "live_kucoin";

export interface AutostartTask {
  installed: boolean;
  state: string | null;
  last_run: string | null;
  last_result: number | null;
  next_run: string | null;
  command: string | null;
  working_dir: string | null;
  task_name: string;
}

export interface AutostartInfo {
  supported: boolean;
  error: string | null;
  tasks: Partial<Record<AutostartKey, AutostartTask>>;
}

export interface ProfileInfo {
  name: string;
  description: string;
  strategy: string;
  sources: string[];
}

export interface Metrics {
  n_trades: number;
  win_rate: number;
  profit_factor: number | null;
  expectancy_pct: number;
  total_return: number;
  cagr: number;
  max_drawdown: number;
  sharpe: number;
  sortino: number | null;
  exposure: number;
  years: number;
}

export interface BacktestResult {
  profile: string;
  description: string;
  per_symbol: { symbol: string; metrics: Metrics; blocked: Record<string, number> }[];
  pooled: Metrics;
  monte_carlo: {
    n_sims: number;
    max_dd_p50: number;
    max_dd_p95: number;
    total_return_p5: number;
    total_return_p50: number;
    prob_loss: number;
  };
  sensitivity_worst: number | null;
  gate: { passed: boolean; checks: Record<string, boolean>; failures: string[] };
  equity: Point[];
  trades: {
    symbol: string;
    entry_time: string;
    exit_time: string;
    return_pct: number;
    pnl_pct: number;
    bars_held: number;
    exit_reason: string;
  }[];
  n_trades_total: number;
}

export interface BacktestParams {
  profile: string;
  symbols: string[];
  timeframe: string;
  days: number;
  train_days: number;
  test_days: number;
  optimize: boolean;
  sensitivity: boolean;
  slippage_bps: number;
}

export interface BacktestRun {
  id: number;
  status: "QUEUED" | "RUNNING" | "DONE" | "FAILED" | "CANCELLED";
  params: BacktestParams;
  error: string | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary?: { gate_passed: boolean; pooled: Metrics };
  result?: BacktestResult;
}

export interface BaselineInfo {
  file: string;
  profile: string;
  timeframe: string;
  symbols: string[];
  n: number;
  mean: number | null;
  gate_passed: boolean;
  created_at: string;
}

export interface Coverage {
  symbol: string;
  metric: string;
  rows: number;
  from: string;
  to: string;
}

export interface SourcesHealth {
  failures: { source: string; last_error_at: string; error: string; count: number }[];
  last_tick_at: string | null;
  last_data_problems: string[] | null;
}

export interface AdminActionRow {
  id: number;
  ts: string;
  username: string;
  action: string;
  account: string | null;
  detail: Record<string, unknown> | null;
  ok: boolean;
}

export interface User {
  id: number;
  username: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export type CollectorStatus = "healthy" | "degraded" | "failing" | "stopped" | "never";
export type RunStatus = "RUNNING" | "OK" | "PARTIAL" | "FAILED" | "INTERRUPTED";

export interface CollectorRun {
  id: number;
  collector: string;
  trigger: "schedule" | "manual" | "cli";
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  symbols_total: number;
  symbols_failed: number;
  rows_added: number;
  rows_purged: number;
  backfilled: string[];
  errors: Record<string, string>;
  error: string | null;
  pid: number | null;
}

export interface Freshness {
  symbol: string;
  metric: string;
  configured: boolean;
  rows: number;
  first: string | null;
  last: string | null;
  lag_periods: number | null;
  missing_periods: number | null;
  status: "ok" | "stale" | "missing";
}

export interface CollectorHealth {
  name: string;
  title: string;
  source: string;
  metrics: string[];
  interval_minutes: number;
  schedule_minute: number;
  exchange_history_days: number;
  retention_days: number;
  symbols: string[];
  status: CollectorStatus;
  issues: string[];
  process: { running: boolean; pid: number | null; started_at: string | null };
  last_run: CollectorRun | null;
  last_success_at: string | null;
  next_run_at: string | null;
  consecutive_failures: number;
  stats_24h: {
    runs: number;
    ok: number;
    partial: number;
    failed: number;
    rows_added: number;
    rows_purged: number;
    avg_duration_ms: number | null;
  };
  data: {
    rows: number;
    oldest: string | null;
    newest: string | null;
    stale_symbols: string[];
    missing_symbols: string[];
    missing_periods: number;
  };
  freshness?: Freshness[];
  log?: string[];
  checked_at: string;
}


export interface RunsPage {
  items: CollectorRun[];
  total: number;
  limit: number;
  offset: number;
}

export interface PnlTradeRef {
  id: number;
  symbol: string;
  account: string;
  pnl: number;
  return_pct: number | null;
  exit_time: string;
}

export interface PnlOpenPosition {
  id: number;
  account: string;
  symbol: string;
  entry_time: string;
  entry_price: number;
  qty: number;
  cost: number;
  price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  stop: number | null;
  stop_distance_pct: number | null;
  hours_open: number;
}

export interface PnlDashboard {
  days: number;
  account: string | null;
  since: string | null;
  summary: {
    total: number;
    realized: number;
    unrealized: number;
    unpriced_positions: number;
    fees: number;
    closed: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    avg_win: number | null;
    avg_loss: number | null;
    profit_factor: number | null;
    expectancy: number | null;
    avg_hours_held: number | null;
    open: number;
    open_cost: number;
    best: PnlTradeRef | null;
    worst: PnlTradeRef | null;
  };
  cumulative: { t: string; cum: number; pnl: number; symbol: string | null; id: number | null }[];
  by_symbol: { symbol: string; realized: number; unrealized: number; total: number; closed: number; wins: number; open: number }[];
  by_reason: { reason: string; count: number; pnl: number; wins: number }[];
  open_positions: PnlOpenPosition[];
  accounts: string[];
  generated_at: string;
}

export type ReportKind = "day" | "week" | "month" | "custom";

export interface ReportMeta {
  id: number;
  kind: ReportKind;
  title: string;
  account: string | null;
  period_start: string;
  period_end: string;
  created_at: string;
  created_by: string;
  sent_at: string | null;
  realized: number;
  closed: number;
  win_rate: number | null;
  complete: boolean;
}

export interface ReportTrade {
  id: number;
  account: string;
  symbol: string;
  entry_time: string;
  entry_price: number;
  exit_time: string | null;
  exit_price: number | null;
  qty: number;
  pnl: number | null;
  return_pct: number | null;
  fees: number;
  exit_reason: string | null;
  hours_held: number | null;
}

export interface ReportData {
  title: string;
  kind: ReportKind;
  account: string | null;
  period: { start: string; end: string; days: number; complete: boolean };
  generated_at: string;
  generated_by: string;
  summary: {
    closed: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    realized: number;
    gross_win: number;
    gross_loss: number;
    fees: number;
    profit_factor: number | null;
    expectancy: number | null;
    avg_win: number | null;
    avg_loss: number | null;
    avg_hours_held: number | null;
    longest_losing_streak: number;
    best: ReportTrade | null;
    worst: ReportTrade | null;
    opened: number;
    open_at_end: number;
  };
  equity: {
    account: string;
    start: number;
    end: number;
    change: number;
    return_pct: number | null;
    max_drawdown: number;
    high: number;
    low: number;
    points: { t: string; v: number }[];
  }[];
  daily: { date: string; pnl: number; trades: number; wins: number; cum: number }[];
  by_symbol: { symbol: string; pnl: number; trades: number; wins: number; fees: number }[];
  by_reason: { reason: string; pnl: number; count: number; wins: number }[];
  trades: ReportTrade[];
  trades_truncated: number;
  open_at_end: ReportTrade[];
  risk_events: { ts: string; account: string; type: string; reason: string | null }[];
  collector: { runs: number; ok: number; partial: number; failed: number; rows_added: number; rows_purged: number };
  admin_actions: { ts: string; username: string; action: string; account: string | null; ok: boolean; detail: unknown }[];
}

export interface ReportFull extends ReportMeta {
  data: ReportData;
}

export interface ReportsPage {
  items: ReportMeta[];
  total: number;
  limit: number;
  offset: number;
}
