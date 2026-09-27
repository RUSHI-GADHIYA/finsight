"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fiscalYear } from "@/lib/format";
import type { FinancialYear } from "@/lib/types";

const MARGINS = [
  { key: "gross_margin", label: "Gross margin" },
  { key: "operating_margin", label: "Operating margin" },
  { key: "net_margin", label: "Net margin" },
] as const;

const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
];

type Row = { fy: string } & Record<string, number | string | null>;

function rows(
  financials: Record<string, FinancialYear[]>,
  key: (typeof MARGINS)[number]["key"],
) {
  const byYear = new Map<string, Row>();
  for (const [ticker, years] of Object.entries(financials)) {
    for (const y of years) {
      const fy = fiscalYear(y.fiscal_year_end);
      const row = byYear.get(fy) ?? { fy };
      const v = y[key];
      row[ticker] = v === null ? null : Math.round(v * 1000) / 10;
      byYear.set(fy, row);
    }
  }
  return [...byYear.values()].sort((a, b) => a.fy.localeCompare(b.fy));
}

/** Small multiples: one panel per margin, one line per company (SEC XBRL, annual 10-K). */
export function MarginsChart({
  financials,
}: {
  financials: Record<string, FinancialYear[]>;
}) {
  const tickers = Object.keys(financials);
  if (tickers.length === 0) return null;
  return (
    <figure className="border border-rule bg-sheet p-4">
      <figcaption className="mb-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="display text-sm font-bold uppercase tracking-widest">
          Margins by fiscal year
        </span>
        {tickers.map((t, i) => (
          <span key={t} className="flex items-center gap-1.5 font-mono text-xs">
            <span
              className="inline-block h-0.5 w-4"
              style={{ background: SERIES[i % 4] }}
            />
            {t}
          </span>
        ))}
        <span className="ml-auto font-mono text-[0.7rem] text-graphite">
          Source: SEC XBRL
        </span>
      </figcaption>
      <div className="grid gap-4 sm:grid-cols-3">
        {MARGINS.map((m) => (
          <div key={m.key}>
            <p className="font-mono text-xs text-graphite">{m.label}, %</p>
            <div className="h-36">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={rows(financials, m.key)}
                  margin={{ top: 8, right: 8, left: -18, bottom: 0 }}
                >
                  <CartesianGrid
                    stroke="var(--rule)"
                    strokeDasharray="2 3"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="fy"
                    tick={{ fontSize: 11, fill: "var(--graphite)" }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: "var(--graphite)" }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    formatter={(v) => `${v}%`}
                    contentStyle={{
                      fontFamily: "var(--font-plex-mono)",
                      fontSize: 12,
                    }}
                  />
                  {tickers.map((t, i) => (
                    <Line
                      key={t}
                      dataKey={t}
                      stroke={SERIES[i % 4]}
                      strokeWidth={2}
                      dot={{ r: 3 }}
                      connectNulls
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ))}
      </div>
    </figure>
  );
}
