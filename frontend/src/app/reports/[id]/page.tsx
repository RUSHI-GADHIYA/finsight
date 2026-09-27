import Link from "next/link";
import { notFound } from "next/navigation";

import { ReportView } from "@/components/ReportView";
import { getReport } from "@/lib/api";
import { date, usd } from "@/lib/format";

export default async function ReportPage(props: PageProps<"/reports/[id]">) {
  const { id } = await props.params;
  const saved = await getReport(id);
  if (!saved) notFound();
  return (
    <div className="mx-auto max-w-4xl">
      <p className="display text-xs font-bold uppercase tracking-widest text-graphite">
        Question
      </p>
      <p className="mb-6 mt-1 text-lg">{saved.question}</p>
      <ReportView
        report={saved.report}
        context={saved.context}
        warnings={saved.warnings}
        signOff={{ reportId: saved.id, date: date(saved.created_at) }}
      />
      <p className="mt-4 flex flex-wrap gap-4 font-mono text-xs text-graphite">
        <span>Research cost {usd(saved.cost_usd)}</span>
        <span>Run {saved.thread_id}</span>
        <Link href="/reports" className="ml-auto underline hover:text-pencil">
          All reports
        </Link>
      </p>
    </div>
  );
}
