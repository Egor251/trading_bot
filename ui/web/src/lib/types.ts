export type Snapshot = {
  schema?: string;
  empty?: boolean;
  ts?: number;
  halted?: boolean;
  halt_reason?: string;
  mode?: string;
  equity?: number;
  fills?: number;
  gross_exposure?: number;
  active_keys?: string[];
  open_orders?: OrderRow[];
  open_positions?: PositionRow[];
  positions?: PositionRow[];
  exposures?: unknown;
  recent?: TapeEvent[];
  recent_notify?: TapeEvent[];
  ensemble?: {
    weights?: Record<string, number>;
    decisions?: number;
    last?: { action?: string; ticker?: string; strength?: number };
    members?: EnsembleMember[];
    weight_mode?: string;
    strategies?: string[];
    observe?: string[];
    enabled?: boolean;
  };
  plugins?: PluginRow[];
  phantom?: PhantomSnap;
  candles?: {
    ohlc?: Record<string, OhlcBar[] | Record<string, number[]>>;
    bar_counts?: Record<string, number>;
    symbols?: number;
    maxlen?: number;
  };
  equity_curve?: number[];
  catalog?: unknown;
  universe?: { mode?: string; profile?: string; reason?: string };
  config?: Record<string, unknown>;
  risk?: Record<string, unknown>;
  ai?: AiSnap;
  storage?: StorageSnap;
  costs?: Record<string, unknown>;
  strategy_research?: {
    ok?: boolean;
    reason?: string;
    symbol?: string;
    block?: string;
    selected?: string[] | null;
    blockers?: string[];
    config?: Record<string, unknown>;
    candidates?: {
      strategy?: string;
      real_wins?: number;
      random_wins?: number;
      shuffled_wins?: number;
      adjusted_p?: number;
      blockers?: string[];
      window_nets?: number[];
      window_p?: number[];
      window_power_nets?: number[];
    }[];
  };
  [key: string]: unknown;
};

export type TapeEvent = {
  t?: number;
  type?: string;
  source?: string;
  payload?: Record<string, unknown>;
};

export type EnsembleMember = {
  strategy_id?: string;
  action?: string;
  stance?: string;
  weight?: number;
  ticker?: string;
  strength?: number;
  metrics?: Record<string, unknown>;
};

export type PluginRow = {
  id?: string;
  running?: boolean;
  enabled?: boolean;
  kind?: string;
  is_strategy?: boolean;
};

export type OhlcBar = {
  t?: number;
  o?: number;
  h?: number;
  l?: number;
  c?: number;
  v?: number;
};

export type OrderRow = {
  ticker?: string;
  action?: string;
  side?: string;
  quantity?: number;
  price?: number;
  status?: string;
  strategy_id?: string;
};

export type PositionRow = {
  ticker?: string;
  quantity?: number;
  avg_price?: number;
  price?: number;
  pnl?: number;
  strategy_id?: string;
};

export type PhantomSnap = {
  fills?: number;
  running?: boolean;
  grid_running?: boolean;
  last_grid?: Record<string, unknown>[];
  recent_fills?: Record<string, unknown>[];
  strategy_schemas?: Record<string, unknown>;
  start_equity?: number;
  [key: string]: unknown;
};

export type AiSnap = {
  ready?: boolean;
  provider?: string;
  default_provider?: string;
  providers?: string[];
  profiles?: unknown;
  chat_profiles?: Array<{
    id?: string;
    display_name?: string;
    provider_type?: string;
    enabled?: boolean;
    ready?: boolean;
  }>;
  tools?: unknown;
  last?: unknown;
  [key: string]: unknown;
};

export type StorageSnap = {
  ready?: boolean;
  default_backend?: string;
  backend_ids?: string[];
  backend_by_class?: Record<string, string>;
  backends?: Record<string, { enabled?: boolean; path?: string; url?: string }>;
  [key: string]: unknown;
};
