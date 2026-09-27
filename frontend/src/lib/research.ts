// Turns the backend's research events into UI state. Pure, so it is unit-tested with a
// recorded event sequence (see research.test.ts).

import type { SSEEvent } from "./sse";
import type { CacheHit, PendingReview, RunStatus } from "./types";

export const STEPS = [
  "supervisor",
  "filings",
  "market",
  "analyst",
  "critic",
  "human_review",
] as const;
export type StepName = (typeof STEPS)[number];

export interface Step {
  status: "pending" | "running" | "done";
  runs: number; // the analyst and critic run again on each revision
  startedAt?: number;
  endedAt?: number;
  costUsd: number;
  summary?: Record<string, unknown>;
}

export type Phase =
  "idle" | "running" | "review" | "resuming" | "done" | "error";

export interface Outcome {
  status: string | null; // approved | rejected | no_data
  reportId: number | null;
  message: string | null;
}

export interface ResearchState {
  phase: Phase;
  question: string;
  threadId: string | null;
  steps: Record<StepName, Step>;
  tokens: number;
  costUsd: number;
  pending: PendingReview | null;
  outcome: Outcome | null;
  error: string | null;
  restored: boolean; // hydrated from a checkpoint rather than streamed
  cacheHit: CacheHit | null; // answered from a signed-off report, no agents ran
  blocked: string | null; // the API refused the question (guardrail)
}

export type ResearchAction =
  | { type: "start"; question: string }
  | { type: "reset" }
  | { type: "resume" }
  | { type: "event"; event: SSEEvent; at: number }
  | { type: "failed"; message: string }
  | { type: "blocked"; message: string }
  | { type: "hydrate"; status: RunStatus };

function emptySteps(): Record<StepName, Step> {
  return Object.fromEntries(
    STEPS.map((name) => [name, { status: "pending", runs: 0, costUsd: 0 }]),
  ) as Record<StepName, Step>;
}

export function initialState(question = ""): ResearchState {
  return {
    phase: "idle",
    question,
    threadId: null,
    steps: emptySteps(),
    tokens: 0,
    costUsd: 0,
    pending: null,
    outcome: null,
    error: null,
    restored: false,
    cacheHit: null,
    blocked: null,
  };
}

const isStep = (name: unknown): name is StepName =>
  typeof name === "string" && (STEPS as readonly string[]).includes(name);

function updateStep(
  state: ResearchState,
  name: StepName,
  patch: Partial<Step>,
): ResearchState {
  return {
    ...state,
    steps: { ...state.steps, [name]: { ...state.steps[name], ...patch } },
  };
}

function onEvent(
  state: ResearchState,
  { event, data }: SSEEvent,
  at: number,
): ResearchState {
  const d = (data ?? {}) as Record<string, unknown>;
  switch (event) {
    case "run_started":
      return { ...state, threadId: String(d.thread_id) };
    case "node_started": {
      if (!isStep(d.node)) return state;
      const step = state.steps[d.node];
      return updateStep(state, d.node, {
        status: "running",
        runs: step.runs + 1,
        startedAt: at,
        endedAt: undefined,
      });
    }
    case "token":
      return { ...state, tokens: state.tokens + 1 };
    case "cost": {
      const cost = Number(d.call_usd ?? 0);
      const next = { ...state, costUsd: state.costUsd + cost };
      return isStep(d.node)
        ? updateStep(next, d.node, {
            costUsd: state.steps[d.node].costUsd + cost,
          })
        : next;
    }
    case "node_finished": {
      if (!isStep(d.node)) return state;
      const { node, ...summary } = d;
      return updateStep(state, node as StepName, {
        status: "done",
        endedAt: at,
        summary,
      });
    }
    case "awaiting_approval": {
      const pending = d as unknown as PendingReview;
      return {
        ...updateStep(state, "human_review", {
          status: "running",
          startedAt: at,
        }),
        phase: "review",
        pending,
        costUsd: Math.max(state.costUsd, pending.cost_usd ?? 0),
      };
    }
    case "done":
      return {
        ...updateStep(state, "human_review", {
          status:
            state.steps.human_review.status === "pending" ? "pending" : "done",
          endedAt: at,
        }),
        phase: "done",
        outcome: {
          status: (d.status as string) ?? null,
          reportId: (d.report_id as number) ?? null,
          message: (d.message as string) ?? null,
        },
      };
    case "cache_hit":
      return { ...state, cacheHit: d as unknown as CacheHit };
    case "error":
      return {
        ...state,
        phase: "error",
        error: String(d.message ?? "Research run failed"),
      };
    default:
      return state;
  }
}

export function researchReducer(
  state: ResearchState,
  action: ResearchAction,
): ResearchState {
  switch (action.type) {
    case "start":
      return { ...initialState(action.question), phase: "running" };
    case "reset":
      return initialState();
    case "resume":
      return { ...state, phase: "resuming", error: null };
    case "event":
      return onEvent(state, action.event, action.at);
    case "failed":
      return { ...state, phase: "error", error: action.message };
    case "blocked":
      return { ...state, phase: "error", blocked: action.message };
    case "hydrate": {
      // Reopened from a checkpoint: no per-step history, only where the run stands now.
      const s = action.status;
      const base = {
        ...initialState(s.question),
        threadId: s.thread_id,
        costUsd: s.cost_usd,
        restored: true,
      };
      const finished = Object.fromEntries(
        STEPS.map((n) => [
          n,
          { status: "done", runs: 1, costUsd: 0 } satisfies Step,
        ]),
      ) as Record<StepName, Step>;
      if (s.pending) {
        finished.human_review = { status: "running", runs: 1, costUsd: 0 };
        return {
          ...base,
          steps: finished,
          phase: "review",
          pending: s.pending,
        };
      }
      if (s.status === "running") return { ...base, phase: "running" };
      return {
        ...base,
        steps: finished,
        phase: "done",
        outcome: {
          status: s.status,
          reportId: s.report_id,
          message: s.message,
        },
      };
    }
  }
}

/** Claim numbers (1-based, reading order) the critic could not verify; 0 = the summary. */
export function flaggedClaims(warnings: string[]): Set<number> {
  const flagged = new Set<number>();
  for (const w of warnings) {
    if (w.startsWith("Summary")) flagged.add(0);
    const m = /^Claim (\d+)/.exec(w);
    if (m) flagged.add(Number(m[1]));
  }
  return flagged;
}
