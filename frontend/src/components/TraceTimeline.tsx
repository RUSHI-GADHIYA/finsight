"use client";

import { useEffect, useState } from "react";

import { seconds, usd } from "@/lib/format";
import type { ResearchState, Step, StepName } from "@/lib/research";

const LABELS: Record<StepName, { title: string; role: string }> = {
  supervisor: { title: "Plan", role: "Supervisor · gpt-5-mini" },
  filings: { title: "Read filings", role: "Filings agent · MCP search" },
  market: { title: "Pull figures", role: "Market agent · SEC XBRL" },
  analyst: { title: "Draft report", role: "Analyst · gpt-5" },
  critic: { title: "Check claims", role: "Critic · rules + gpt-5-mini" },
  human_review: { title: "Your sign-off", role: "Human review" },
};

const list = (v: unknown) => (Array.isArray(v) ? (v as string[]) : []);

function describe(name: StepName, step: Step, tokens: number): string | null {
  const s = step.summary ?? {};
  if (name === "analyst" && step.status === "running") {
    return tokens
      ? `Writing… ${tokens.toLocaleString()} tokens`
      : "Reading sources…";
  }
  if (name === "human_review" && step.status === "running")
    return "Waiting for your review";
  if (step.status !== "done" || !step.summary) return null; // restored runs keep no details
  switch (name) {
    case "supervisor": {
      if (s.message) return String(s.message);
      const needs = [s.financials && "financials", s.prices && "prices"].filter(
        Boolean,
      );
      return `${list(s.tickers).join(" · ")} — ${list(s.queries).length} searches${
        needs.length ? `, ${needs.join(" and ")}` : ""
      }`;
    }
    case "filings":
      return `${s.passages ?? 0} passages retrieved`;
    case "market": {
      const parts = [];
      if (list(s.financials).length)
        parts.push(`financials for ${list(s.financials).join(", ")}`);
      if (list(s.prices).length)
        parts.push(`prices for ${list(s.prices).join(", ")}`);
      return parts.length ? parts.join("; ") : "No figures needed";
    }
    case "analyst":
      return `${s.claims} claims${Number(s.revision) > 0 ? ` · revision ${s.revision}` : ""}`;
    case "critic": {
      const flagged = Number(s.claims) - Number(s.supported);
      return `${s.supported} of ${s.claims} verified${flagged ? ` · ${flagged} sent back` : ""}`;
    }
    case "human_review":
      return s.status === "approved"
        ? "Signed off"
        : s.status === "rejected"
          ? "Rejected"
          : null;
  }
}

function StepRow({
  name,
  step,
  tokens,
  now,
}: {
  name: StepName;
  step: Step;
  tokens: number;
  now: number;
}) {
  const label = LABELS[name];
  const detail = describe(name, step, tokens);
  const elapsed =
    step.startedAt !== undefined && name !== "human_review"
      ? (step.endedAt ?? now) - step.startedAt
      : null;
  const errors = list(step.summary?.errors);
  return (
    <li className={step.status === "pending" ? "opacity-45" : ""}>
      <div className="flex items-baseline gap-2">
        <span
          aria-hidden
          className={`inline-block h-2 w-2 shrink-0 rounded-full ${
            step.status === "done"
              ? "bg-ink"
              : step.status === "running"
                ? "running-dot bg-pencil"
                : "border border-graphite"
          }`}
        />
        <span className="display text-base font-semibold uppercase">
          {label.title}
        </span>
        {step.runs > 1 && (
          <span className="font-mono text-xs text-pencil">×{step.runs}</span>
        )}
        <span className="ml-auto font-mono text-xs text-graphite">
          {elapsed !== null && seconds(Math.max(0, elapsed))}
          {step.costUsd > 0 && ` · ${usd(step.costUsd)}`}
        </span>
      </div>
      <p className="ml-4 font-mono text-[0.7rem] uppercase tracking-wide text-graphite">
        {label.role}
      </p>
      {detail && <p className="ml-4 mt-0.5 text-sm leading-snug">{detail}</p>}
      {errors.map((e) => (
        <p key={e} className="ml-4 mt-0.5 text-sm text-pencil">
          {e}
        </p>
      ))}
    </li>
  );
}

export function TraceTimeline({ state }: { state: ResearchState }) {
  const active = state.phase === "running" || state.phase === "resuming";
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [active]);

  const row = (name: StepName) => (
    <StepRow
      name={name}
      step={state.steps[name]}
      tokens={state.tokens}
      now={now}
    />
  );
  return (
    <section aria-label="Agent trace" aria-live="polite">
      <h2 className="display mb-3 flex items-baseline text-sm font-bold uppercase tracking-widest text-graphite">
        Agent trace
        <span className="ml-auto font-mono text-xs normal-case tracking-normal">
          {usd(state.costUsd)} spent
        </span>
      </h2>
      {state.restored && (
        <p className="mb-3 text-sm text-graphite">
          Reopened from a saved checkpoint. Step details were not kept.
        </p>
      )}
      <ol className="space-y-4 border-l border-rule pl-4">
        {row("supervisor")}
        <li>
          {/* The two data agents run in parallel. */}
          <p className="mb-2 font-mono text-[0.7rem] uppercase tracking-wide text-graphite">
            In parallel
          </p>
          <ol className="space-y-4 border-l-2 border-dotted border-rule pl-3">
            {row("filings")}
            {row("market")}
          </ol>
        </li>
        {row("analyst")}
        {row("critic")}
        {row("human_review")}
      </ol>
    </section>
  );
}
