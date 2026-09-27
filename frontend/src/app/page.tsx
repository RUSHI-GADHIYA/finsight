import { connection } from "next/server";

import { RecentReports } from "@/components/RecentReports";
import { ResearchSession } from "@/components/ResearchSession";
import { getHealth, listReports } from "@/lib/api";
import type { SavedReport } from "@/lib/types";

export default async function Home() {
  await connection(); // request-time data: never prerender at build
  let recent: SavedReport[] | null = null;
  try {
    recent = (await listReports())?.slice(0, 5) ?? [];
  } catch {
    recent = null; // API down: the page still works, the list says so
  }
  const health = await getHealth().catch(() => null);
  return (
    <ResearchSession llmConfigured={health?.llm_configured ?? true}>
      <RecentReports reports={recent} />
    </ResearchSession>
  );
}
