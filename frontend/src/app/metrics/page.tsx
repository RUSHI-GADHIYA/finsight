import type { Metadata } from "next";
import { connection } from "next/server";

import { CostChart } from "@/components/CostChart";
import { getMetrics } from "@/lib/api";
import { seconds, usd } from "@/lib/format";
import type { Metrics } from "@/lib/types";

export const metadata: Metadata = { title: "Metrics · FinSight" };

function Schedule({
  title,
  rows,
}: {
  title: string;
  rows: [string, string, string?][];
}) {
  return (
    <section>
      <h2 className="display border-b-2 border-ink pb-1 text-sm font-bold uppercase tracking-widest">
        {title}
      </h2>
      <table className="w-full text-sm">
        <tbody>
          {rows.map(([label, value, note]) => (
            <tr key={label} className="border-b border-rule">
              <th scope="row" className="py-2 pr-4 text-left font-normal">
                {label}
                {note && (
                  <span className="block font-mono text-[0.7rem] text-graphite">
                    {note}
                  </span>
                )}
              </th>
              <td className="py-2 text-right font-mono tabular-nums">
                {value}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
const ms = (v: number | null) => (v === null ? "—" : seconds(v));

export default async function MetricsPage() {
  await connection();
  let m: Metrics | null = null;
  try {
    m = await getMetrics();
  } catch {
    m = null;
  }
  if (!m) {
    return (
      <p className="text-graphite">
        Metrics are unavailable: the research API isn&apos;t reachable.
      </p>
    );
  }
  const retrieval = m.evals.retrieval;
  const answers = m.evals.answers;
  return (
    <div className="space-y-10">
      <header>
        <h1 className="display text-4xl font-bold uppercase">Metrics</h1>
        <p className="mt-2 max-w-2xl text-graphite">
          Cost, speed and safety of the last {m.runs} research runs, and the
          latest offline evaluation scores.
        </p>
      </header>

      <div className="grid gap-10 md:grid-cols-2">
        <Schedule
          title="Runs"
          rows={[
            ["Research runs", String(m.runs)],
            ["Total LLM spend", usd(m.total_cost_usd)],
            [
              "Cost per run",
              usd(m.mean_cost_usd),
              "runs that called the models",
            ],
            [
              "Time to review, median",
              ms(m.latency_p50_ms),
              "question to report ready",
            ],
            ["Time to review, 95th percentile", ms(m.latency_p95_ms)],
            [
              "Answered from cache",
              pct(m.cache_hit_rate),
              "signed-off reports reused, $0",
            ],
          ]}
        />
        <div className="space-y-10">
          <Schedule
            title="Guardrails"
            rows={[
              [
                "Questions blocked",
                String(m.guardrails.input_blocked ?? 0),
                "prompt-injection classifier",
              ],
              [
                "Filing passages dropped",
                String(m.guardrails.passage_dropped ?? 0),
                "instructions hidden in retrieved text",
              ],
              [
                "Questions with personal data redacted",
                String(m.guardrails.pii_redacted ?? 0),
              ],
            ]}
          />
          <Schedule
            title="Sign-off"
            rows={[
              ["Approved as written", String(m.reviews.report_approved ?? 0)],
              ["Approved with edits", String(m.reviews.report_edited ?? 0)],
              ["Rejected", String(m.reviews.report_rejected ?? 0)],
            ]}
          />
        </div>
      </div>

      <section>
        <h2 className="display border-b-2 border-ink pb-1 text-sm font-bold uppercase tracking-widest">
          Cost per run, oldest to newest
        </h2>
        <p className="mt-1 font-mono text-[0.7rem] text-graphite">
          <span className="text-[var(--series-1)]">■</span> agents ran ·{" "}
          <span className="text-[var(--series-2)]">■</span> answered from cache
        </p>
        <CostChart runs={m.recent} />
      </section>

      <div className="grid gap-10 md:grid-cols-2">
        {retrieval && (
          <Schedule
            title={`Retrieval eval · ${retrieval.questions} questions · ${retrieval.run_date}`}
            rows={Object.entries(retrieval.configs).map(([name, c]) => [
              name,
              `Hit@5 ${c.hit_at_5.toFixed(2)} · MRR ${c.mrr_at_10.toFixed(2)}`,
              `${c.seconds_per_query.toFixed(2)}s per query on CPU`,
            ])}
          />
        )}
        {answers && (
          <Schedule
            title={`Answer eval · ${answers.questions} questions · ${answers.run_date}`}
            rows={[
              [
                "Faithfulness",
                answers.faithfulness.toFixed(2),
                `${answers.judged} claims judged by gpt-5-mini`,
              ],
              ["Source passage cited", answers.citation_hit.toFixed(2)],
              ["Source passage retrieved", answers.evidence_hit.toFixed(2)],
              [
                "Cost per question",
                usd(answers.mean_cost_usd),
                "including the judge",
              ],
            ]}
          />
        )}
      </div>
    </div>
  );
}
