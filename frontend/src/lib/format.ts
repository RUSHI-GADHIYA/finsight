import type { Figure, Metric } from "./types";

export const SECTION_LABELS: Record<string, string> = {
  business: "Item 1 · Business",
  risk_factors: "Item 1A · Risk Factors",
  legal_proceedings: "Item 3 · Legal Proceedings",
  mdna: "Item 7 · MD&A",
  market_risk: "Item 7A · Market Risk",
};

const METRIC_LABELS: Record<Metric, string> = {
  revenue_musd: "revenue",
  gross_profit_musd: "gross profit",
  operating_income_musd: "operating income",
  net_income_musd: "net income",
  gross_margin_pct: "gross margin",
  operating_margin_pct: "operating margin",
  net_margin_pct: "net margin",
  price_return_pct: "share price return",
};

export function usd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(3)}`;
}

/** Fiscal year label from a fiscal year end: NVIDIA's FY2025 ends 2025-01-26. */
export function fiscalYear(fye: string): string {
  return fye ? `FY${fye.slice(0, 4)}` : "";
}

export function millions(musd: number): string {
  return Math.abs(musd) >= 1000
    ? `$${(musd / 1000).toFixed(1)}B`
    : `$${musd.toFixed(0)}M`;
}

export function figureLabel(f: Figure): { what: string; value: string } {
  const value = f.metric.endsWith("_pct")
    ? `${f.value.toFixed(1)}%`
    : millions(f.value);
  const when =
    f.metric === "price_return_pct" ? "" : ` ${fiscalYear(f.fiscal_year_end)}`;
  return { what: `${f.ticker} ${METRIC_LABELS[f.metric]}${when}`, value };
}

export function seconds(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
}

export function date(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
