import type { Metadata } from "next";
import { connection } from "next/server";

import { RecentReports } from "@/components/RecentReports";
import { listReports } from "@/lib/api";
import type { SavedReport } from "@/lib/types";

export const metadata: Metadata = { title: "Signed-off reports · FinSight" };

export default async function ReportsPage() {
  await connection();
  let reports: SavedReport[] | null = null;
  try {
    reports = (await listReports()) ?? [];
  } catch {
    reports = null;
  }
  return (
    <>
      <h1 className="display text-4xl font-bold uppercase">
        Signed-off reports
      </h1>
      <p className="mt-2 max-w-2xl text-graphite">
        Every report here was checked claim by claim and approved by a reviewer
        before it was saved.
      </p>
      <RecentReports reports={reports} heading="All reports" />
    </>
  );
}
