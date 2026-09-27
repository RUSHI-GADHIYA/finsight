import Link from "next/link";

import { date, usd } from "@/lib/format";
import type { SavedReport } from "@/lib/types";

/** Signed-off reports; `null` means the API could not be reached. */
export function RecentReports({
  reports,
  heading = "Recently signed off",
}: {
  reports: SavedReport[] | null;
  heading?: string;
}) {
  return (
    <section className="mt-14">
      <h2 className="display border-b border-rule pb-2 text-sm font-bold uppercase tracking-widest text-graphite">
        {heading}
      </h2>
      {reports === null ? (
        <p className="mt-3 text-graphite">
          The research API isn&apos;t reachable. Start it with{" "}
          <code className="font-mono text-sm">uv run uvicorn app.main:app</code>{" "}
          in <code className="font-mono text-sm">backend/</code>.
        </p>
      ) : reports.length === 0 ? (
        <p className="mt-3 text-graphite">
          No reports yet. Ask a question above and sign off on the result.
        </p>
      ) : (
        <ul className="divide-y divide-rule">
          {reports.map((r) => (
            <li key={r.id}>
              <Link
                href={`/reports/${r.id}`}
                className="group flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3"
              >
                <span className="font-mono text-xs text-graphite">#{r.id}</span>
                <span className="display text-lg font-semibold uppercase group-hover:text-pencil">
                  {r.report.title}
                </span>
                <span className="ml-auto font-mono text-xs text-graphite">
                  {date(r.created_at)} · {usd(r.cost_usd)}
                  {r.warnings.length > 0 && (
                    <span className="ml-2 text-pencil">
                      {r.warnings.length} unverified
                    </span>
                  )}
                </span>
                <span className="basis-full pl-8 text-sm text-graphite">
                  {r.question}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
