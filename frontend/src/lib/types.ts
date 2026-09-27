// Mirrors the backend's Pydantic models (backend/app/agents/state.py, app/rag/*).

export type Metric =
  | "revenue_musd"
  | "gross_profit_musd"
  | "operating_income_musd"
  | "net_income_musd"
  | "gross_margin_pct"
  | "operating_margin_pct"
  | "net_margin_pct"
  | "price_return_pct";

export interface Figure {
  ticker: string;
  fiscal_year_end: string; // "" for price returns
  metric: Metric;
  value: number;
}

export interface Claim {
  text: string;
  chunk_ids: number[];
  figures: Figure[];
}

export interface ReportSection {
  heading: string;
  claims: Claim[];
}

export interface Report {
  title: string;
  summary: string;
  sections: ReportSection[];
}

export interface Citation {
  chunk_id: number;
  ticker: string;
  form: string;
  report_date: string | null;
  section: string;
  url: string;
}

export interface RetrievedChunk {
  citation: Citation;
  text: string;
  score: number;
}

export interface FinancialYear {
  fiscal_year_end: string;
  revenue: number | null;
  gross_profit: number | null;
  operating_income: number | null;
  net_income: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  source_accession: string | null;
}

export interface PriceSummary {
  ticker: string;
  period: string;
  start_close: number;
  end_close: number;
  total_return: number;
  source: string;
}

export interface ReportContext {
  sources: RetrievedChunk[];
  financials: Record<string, FinancialYear[]>;
  prices: Record<string, PriceSummary>;
  errors: string[];
}

/** Payload of the `awaiting_approval` event (and `pending` in a run status). */
export interface PendingReview {
  thread_id?: string;
  question: string;
  report: Report;
  context: ReportContext;
  warnings: string[];
  cost_usd: number;
  disclaimer: string;
}

export interface RunStatus {
  thread_id: string;
  question: string;
  status: string;
  pending: PendingReview | null;
  report_id: number | null;
  message: string | null;
  cost_usd: number;
}

export interface SavedReport {
  id: number;
  thread_id: string;
  question: string;
  report: Report;
  warnings: string[];
  cost_usd: number;
  context: ReportContext | null;
  created_at: string;
}

export type ReviewAction =
  | { action: "approve" }
  | { action: "reject" }
  | { action: "edit"; report: Report };

export interface CacheHit {
  report_id: number;
  question: string;
  similarity: number;
  cached_at: string;
}

export interface RunPoint {
  created_at: string;
  status: string;
  cost_usd: number;
  latency_ms: number | null;
  cache_hit: boolean;
}

export interface Metrics {
  runs: number;
  total_cost_usd: number;
  mean_cost_usd: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  cache_hit_rate: number;
  guardrails: Record<string, number>;
  reviews: Record<string, number>;
  recent: RunPoint[];
  evals: {
    retrieval?: {
      run_date: string;
      questions: number;
      configs: Record<
        string,
        { hit_at_5: number; mrr_at_10: number; seconds_per_query: number }
      >;
    };
    answers?: {
      run_date: string;
      questions: number;
      evidence_hit: number;
      citation_hit: number;
      faithfulness: number;
      judged: number;
      mean_cost_usd: number;
    };
  };
}
