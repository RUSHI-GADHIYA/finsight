import Link from "next/link";

import { ResearchSession } from "@/components/ResearchSession";
import { getRunStatus } from "@/lib/api";

// Reopening a run (refresh, shared link, or after a server restart): the backend restores it
// from its LangGraph checkpoint, including a review that is still waiting for sign-off.
export default async function ResearchRunPage(
  props: PageProps<"/research/[threadId]">,
) {
  const { threadId } = await props.params;
  const status = await getRunStatus(threadId).catch(() => null);
  if (!status) {
    return (
      <div className="max-w-2xl">
        <h1 className="display text-3xl font-bold uppercase">Run not found</h1>
        <p className="mt-2 text-graphite">
          There is no research run with this link, or the API isn&apos;t
          reachable.
        </p>
        <Link
          href="/"
          className="display mt-4 inline-block font-semibold uppercase underline"
        >
          Ask a new question
        </Link>
      </div>
    );
  }
  return <ResearchSession initial={status} />;
}
