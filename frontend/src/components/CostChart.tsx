"use client";

import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { RunPoint } from "@/lib/types";

/** Cost of each recent run; cache hits (free) in teal. */
export function CostChart({ runs }: { runs: RunPoint[] }) {
  if (runs.length === 0) return null;
  const data = runs.map((r, i) => ({
    n: i + 1,
    cents: r.cost_usd * 100,
    cache: r.cache_hit,
  }));
  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 8, right: 8, left: -12, bottom: 0 }}
        >
          <XAxis
            dataKey="n"
            tick={false}
            axisLine={{ stroke: "var(--rule)" }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "var(--graphite)" }}
            tickLine={false}
            axisLine={false}
            unit="¢"
          />
          <Tooltip
            formatter={(v) => [`${Number(v).toFixed(2)}¢`, "cost"]}
            labelFormatter={() => ""}
            contentStyle={{ fontFamily: "var(--font-plex-mono)", fontSize: 12 }}
          />
          <Bar dataKey="cents" isAnimationActive={false}>
            {data.map((d) => (
              <Cell
                key={d.n}
                fill={d.cache ? "var(--series-2)" : "var(--series-1)"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
