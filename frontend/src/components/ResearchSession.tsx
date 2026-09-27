"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useReducer, useRef, useState } from "react";

import { resumeResearch, startResearch } from "@/lib/api";
import { usd } from "@/lib/format";
import { initialState, researchReducer } from "@/lib/research";
import type { SSEEvent } from "@/lib/sse";
import type { Report, ReviewAction, RunStatus } from "@/lib/types";

import { ReportView } from "./ReportView";
import { TraceTimeline } from "./TraceTimeline";

const EXAMPLES = [
  "Compare NVIDIA and AMD's data-center risk factors and margins over the last 2 years",
  "How do JPMorgan and Goldman Sachs describe their exposure to interest-rate risk?",
  "What does Tesla say about competition and pricing pressure in its latest 10-K?",
];

export function ResearchSession({
  initial,
  children,
}: {
  initial?: RunStatus;
  children?: ReactNode; // shown under the question form, e.g. recent reports
}) {
  const [state, dispatch] = useReducer(researchReducer, undefined, () =>
    initial
      ? researchReducer(initialState(), { type: "hydrate", status: initial })
      : initialState(),
  );
  const [question, setQuestion] = useState("");
  const [draft, setDraft] = useState<Report | null>(null);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  const onEvent = (event: SSEEvent) => {
    if (event.event === "run_started") {
      // Keep the run addressable across refreshes without remounting this component.
      const id = (event.data as { thread_id: string }).thread_id;
      window.history.replaceState(null, "", `/research/${id}`);
    }
    dispatch({ type: "event", event, at: Date.now() });
  };

  async function run(stream: (signal: AbortSignal) => Promise<void>) {
    abort.current?.abort();
    abort.current = new AbortController();
    try {
      await stream(abort.current.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        dispatch({
          type: "failed",
          message: `Could not reach the research API: ${(e as Error).message}`,
        });
      }
    }
  }

  function ask(q: string) {
    const text = q.trim();
    if (text.length < 5) return;
    dispatch({ type: "start", question: text });
    void run((signal) => startResearch(text, onEvent, signal));
  }

  function reset() {
    abort.current?.abort();
    window.history.replaceState(null, "", "/");
    setQuestion("");
    dispatch({ type: "reset" });
  }

  function review(action: ReviewAction) {
    if (!state.threadId) return;
    const threadId = state.threadId;
    setDraft(null);
    dispatch({ type: "resume" });
    void run((signal) => resumeResearch(threadId, action, onEvent, signal));
  }

  if (state.phase === "idle") {
    return (
      <>
        <AskForm question={question} setQuestion={setQuestion} onAsk={ask} />
        {children}
      </>
    );
  }

  const pending = state.pending;
  const busy = state.phase === "running" || state.phase === "resuming";
  return (
    <div className="grid gap-8 lg:grid-cols-[18rem_1fr]">
      <aside className="lg:sticky lg:top-6 lg:self-start">
        <p className="display text-xs font-bold uppercase tracking-widest text-graphite">
          Question
        </p>
        <p className="mb-6 mt-1 text-lg leading-snug">{state.question}</p>
        <TraceTimeline state={state} />
      </aside>

      <div className="min-w-0 space-y-4">
        {state.phase === "error" && (
          <div role="alert" className="border-l-2 border-pencil bg-sheet p-4">
            <p className="display font-bold uppercase text-pencil">
              The run stopped
            </p>
            <p>{state.error}</p>
            <button
              className="display mt-2 text-sm font-semibold uppercase underline"
              onClick={() => ask(state.question)}
            >
              Run it again
            </button>
          </div>
        )}

        {state.phase === "done" && state.outcome && (
          <Outcome state={state} onAgain={reset} />
        )}

        {pending ? (
          <>
            <ReportView
              report={draft ?? pending.report}
              context={pending.context}
              warnings={pending.warnings}
              editing={draft !== null}
              onChange={setDraft}
            />
            {(state.phase === "review" || state.phase === "resuming") && (
              <ReviewBar
                busy={busy}
                editing={draft !== null}
                costUsd={state.costUsd}
                onApprove={() =>
                  review(
                    draft
                      ? { action: "edit", report: draft }
                      : { action: "approve" },
                  )
                }
                onEdit={() => setDraft(structuredClone(pending.report))}
                onCancelEdit={() => setDraft(null)}
                onReject={() => review({ action: "reject" })}
              />
            )}
          </>
        ) : (
          busy && (
            <div className="workpaper flex min-h-80 items-center border border-rule p-8 pl-17">
              <p className="display text-xl font-semibold uppercase text-graphite">
                The report appears here once the checker has been through it.
              </p>
            </div>
          )
        )}
      </div>
    </div>
  );
}

function AskForm({
  question,
  setQuestion,
  onAsk,
}: {
  question: string;
  setQuestion: (q: string) => void;
  onAsk: (q: string) => void;
}) {
  return (
    <section className="max-w-3xl">
      <h1 className="display text-5xl font-bold uppercase leading-[0.95] sm:text-7xl">
        Ask the filings.
      </h1>
      <p className="mt-4 max-w-2xl text-lg leading-relaxed text-graphite">
        Agents search 24 annual reports from 12 companies and pull the reported
        figures from SEC XBRL data. A checker ticks off every claim it can trace
        to a source, and nothing is saved until you sign off.
      </p>
      <form
        className="mt-8"
        onSubmit={(e) => {
          e.preventDefault();
          onAsk(question);
        }}
      >
        <label
          htmlFor="question"
          className="display text-xs font-bold uppercase tracking-widest text-graphite"
        >
          Your research question
        </label>
        <div className="mt-2 flex flex-col gap-3 sm:flex-row">
          <textarea
            id="question"
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onAsk(question);
              }
            }}
            placeholder="e.g. How exposed is Apple to supply-chain risk in China?"
            className="flex-1 resize-none border border-rule bg-sheet px-3 py-2 text-lg focus:border-ink focus:outline-none"
          />
          <button
            type="submit"
            disabled={question.trim().length < 5}
            className="display bg-ink px-6 py-3 text-lg font-bold uppercase text-sheet hover:bg-pencil disabled:opacity-40"
          >
            Research
          </button>
        </div>
      </form>
      <p className="mt-6 font-mono text-xs uppercase tracking-wide text-graphite">
        Or start from
      </p>
      <ul className="mt-2 space-y-2">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button
              onClick={() => onAsk(q)}
              className="text-left underline decoration-rule underline-offset-4 hover:text-pencil hover:decoration-pencil"
            >
              {q}
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-6 font-mono text-xs text-graphite">
        A run takes 1–2 minutes and costs about 1–4¢.
      </p>
    </section>
  );
}

function ReviewBar(props: {
  busy: boolean;
  editing: boolean;
  costUsd: number;
  onApprove: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onReject: () => void;
}) {
  const btn =
    "display px-4 py-2 text-sm font-bold uppercase disabled:opacity-40";
  return (
    <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border border-rule bg-sheet/95 p-3 backdrop-blur">
      <p className="min-w-0 flex-1 basis-64 text-sm">
        {props.editing
          ? "Edit the wording; citations and figures stay attached to each claim."
          : "Review the report. Signing off saves it to your reports."}
        <span className="ml-2 font-mono text-xs text-graphite">
          {usd(props.costUsd)} so far
        </span>
      </p>
      <div className="ml-auto flex shrink-0 items-center gap-3">
        {props.editing ? (
          <button
            className={`${btn} border border-ink`}
            disabled={props.busy}
            onClick={props.onCancelEdit}
          >
            Discard edits
          </button>
        ) : (
          <>
            <button
              className={`${btn} text-pencil hover:underline`}
              disabled={props.busy}
              onClick={props.onReject}
            >
              Reject
            </button>
            <button
              className={`${btn} border border-ink`}
              disabled={props.busy}
              onClick={props.onEdit}
            >
              Edit
            </button>
          </>
        )}
        <button
          className={`${btn} bg-ink text-sheet hover:bg-pencil`}
          disabled={props.busy}
          onClick={props.onApprove}
        >
          {props.busy
            ? "Saving…"
            : props.editing
              ? "Sign off with edits"
              : "Sign off"}
        </button>
      </div>
    </div>
  );
}

function Outcome({
  state,
  onAgain,
}: {
  state: ReturnType<typeof initialState>;
  onAgain: () => void;
}) {
  const o = state.outcome!;
  const again = (
    <button
      className="display text-sm font-semibold uppercase underline"
      onClick={onAgain}
    >
      Ask another question
    </button>
  );
  if (o.status === "approved" && o.reportId !== null) {
    return (
      <div className="flex flex-wrap items-baseline gap-4 border-l-2 border-ink bg-sheet p-4">
        <p className="display font-bold uppercase">
          Signed off and saved as report #{o.reportId}.
        </p>
        <Link
          href={`/reports/${o.reportId}`}
          className="display text-sm font-semibold uppercase underline"
        >
          View report
        </Link>
        {again}
      </div>
    );
  }
  if (o.status === "rejected") {
    return (
      <div className="flex flex-wrap items-baseline gap-4 border-l-2 border-pencil bg-sheet p-4">
        <p className="display font-bold uppercase">
          Rejected. Nothing was saved.
        </p>
        {again}
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-baseline gap-4 border-l-2 border-pencil bg-sheet p-4">
      <p>{o.message ?? "The run finished without a report."}</p>
      {again}
    </div>
  );
}
