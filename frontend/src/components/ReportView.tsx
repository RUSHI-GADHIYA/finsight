"use client";

import { useCallback, useMemo, useState } from "react";

import { API_URL } from "@/lib/api";
import { figureLabel } from "@/lib/format";
import { flaggedClaims } from "@/lib/research";
import type { Claim, Report, ReportContext, RetrievedChunk } from "@/lib/types";

import { MarginsChart } from "./MarginsChart";
import { SourceDrawer } from "./SourceDrawer";
import { FlagMark, TickMark } from "./TickMark";

interface Props {
  report: Report;
  context: ReportContext | null;
  warnings: string[];
  editing?: boolean;
  onChange?: (report: Report) => void;
  signOff?: { reportId: number; date: string };
}

const field =
  "w-full resize-y rounded-sm border border-rule bg-white/70 px-2 py-1 focus:border-ink focus:outline-none";

export function ReportView({
  report,
  context,
  warnings,
  editing = false,
  onChange,
  signOff,
}: Props) {
  const [open, setOpen] = useState<{
    n: number;
    source: RetrievedChunk;
  } | null>(null);
  const close = useCallback(() => setOpen(null), []);
  const flagged = flaggedClaims(warnings);
  const reasons = new Map(
    warnings.map((w) => [
      /^Claim (\d+)/.exec(w)?.[1] ?? (w.startsWith("Summary") ? "0" : ""),
      w,
    ]),
  );

  // Footnote numbers in order of first citation, like a filing's references.
  const footnotes = useMemo(() => {
    const numbers = new Map<number, number>();
    for (const s of report.sections)
      for (const c of s.claims)
        for (const id of c.chunk_ids)
          if (!numbers.has(id)) numbers.set(id, numbers.size + 1);
    return numbers;
  }, [report]);
  const sources = useMemo(
    () =>
      new Map((context?.sources ?? []).map((s) => [s.citation.chunk_id, s])),
    [context],
  );

  async function openSource(chunkId: number) {
    const n = footnotes.get(chunkId) ?? 0;
    const known = sources.get(chunkId);
    if (known) return setOpen({ n, source: known });
    const resp = await fetch(`${API_URL}/chunks/${chunkId}`); // reports saved before context
    if (resp.ok) setOpen({ n, source: (await resp.json()) as RetrievedChunk });
  }

  const update = (fn: (r: Report) => void) => {
    const next = structuredClone(report);
    fn(next);
    onChange?.(next);
  };

  // Claims are numbered in reading order across sections (the critic's numbering).
  const firstClaimNo = report.sections.map((_, si) =>
    report.sections.slice(0, si).reduce((n, s) => n + s.claims.length, 1),
  );
  const margin = (n: number, delayMs: number) => (
    <div className="flex w-13 shrink-0 justify-center pt-1">
      {flagged.has(n) ? (
        <FlagMark reason={reasons.get(String(n))} />
      ) : (
        <TickMark delayMs={delayMs} />
      )}
    </div>
  );

  return (
    <article className="workpaper relative border border-rule pb-6 shadow-[0_1px_0_var(--rule)]">
      {signOff && (
        <div
          className="display absolute right-5 top-5 rotate-[-4deg] border-2 border-pencil px-3 py-1 text-center font-bold uppercase leading-tight text-pencil"
          aria-label={`Signed off on ${signOff.date}, report ${signOff.reportId}`}
        >
          <span className="block text-sm tracking-widest">Signed off</span>
          <span className="block font-mono text-[0.7rem] font-normal normal-case">
            {signOff.date} · #{signOff.reportId}
          </span>
        </div>
      )}

      <header
        className={`flex gap-0 pt-6 ${signOff ? "pr-6 sm:pr-48" : "pr-6"}`}
      >
        <div className="w-13 shrink-0" />
        <div className="flex-1 pl-4">
          {editing ? (
            <input
              aria-label="Report title"
              className={`${field} display text-2xl font-bold`}
              value={report.title}
              onChange={(e) => update((r) => void (r.title = e.target.value))}
            />
          ) : (
            <h1 className="display max-w-[40ch] text-3xl font-bold uppercase leading-tight sm:text-4xl">
              {report.title}
            </h1>
          )}
        </div>
      </header>

      <section className="mt-5 flex pr-6" aria-label="Summary">
        {margin(0, 0)}
        <div className="flex-1 pl-4">
          <p className="display mb-1 text-xs font-bold uppercase tracking-widest text-graphite">
            Summary
          </p>
          {editing ? (
            <textarea
              aria-label="Summary"
              rows={4}
              className={`${field} text-lg`}
              value={report.summary}
              onChange={(e) => update((r) => void (r.summary = e.target.value))}
            />
          ) : (
            <p className="text-lg leading-relaxed">{report.summary}</p>
          )}
        </div>
      </section>

      {context && Object.keys(context.financials).length > 0 && (
        <div className="mt-6 pl-17 pr-6">
          <MarginsChart financials={context.financials} />
        </div>
      )}

      {report.sections.map((section, si) => (
        <section key={si} className="mt-8 pr-6">
          <h2 className="display pl-17 text-lg font-bold uppercase tracking-wide">
            {section.heading}
          </h2>
          <ul className="mt-2 space-y-3">
            {section.claims.map((claim, ci) => {
              const claimNo = firstClaimNo[si] + ci;
              return (
                <li key={ci} className="flex">
                  {margin(claimNo, 120 + claimNo * 90)}
                  <ClaimBody
                    claim={claim}
                    footnotes={footnotes}
                    editing={editing}
                    onOpen={openSource}
                    onText={(text) =>
                      update(
                        (r) => void (r.sections[si].claims[ci].text = text),
                      )
                    }
                  />
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      {(warnings.length > 0 || (context?.errors.length ?? 0) > 0) && (
        <aside className="mt-8 ml-17 mr-6 border-l-2 border-pencil pl-3 text-sm">
          {warnings.length > 0 && (
            <p className="display mb-1 font-bold uppercase text-pencil">
              Not verified — read these claims closely
            </p>
          )}
          {warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
          {context?.errors.map((e) => (
            <p key={e} className="text-graphite">
              Data gap: {e}
            </p>
          ))}
        </aside>
      )}

      {open && (
        <SourceDrawer number={open.n} source={open.source} onClose={close} />
      )}
    </article>
  );
}

function ClaimBody({
  claim,
  footnotes,
  editing,
  onOpen,
  onText,
}: {
  claim: Claim;
  footnotes: Map<number, number>;
  editing: boolean;
  onOpen: (chunkId: number) => void;
  onText: (text: string) => void;
}) {
  return (
    <div className="flex-1 pl-4">
      {editing ? (
        <textarea
          aria-label="Claim text"
          rows={3}
          className={field}
          value={claim.text}
          onChange={(e) => onText(e.target.value)}
        />
      ) : (
        <p className="leading-relaxed">
          {claim.text}
          {claim.chunk_ids.map((id) => (
            <button
              key={id}
              onClick={() => onOpen(id)}
              className="ml-0.5 align-super font-mono text-[0.7rem] text-pencil hover:underline"
              aria-label={`Open source ${footnotes.get(id)}`}
            >
              [{footnotes.get(id)}]
            </button>
          ))}
        </p>
      )}
      {claim.figures.length > 0 && (
        <p className="mt-1 flex flex-wrap gap-1.5">
          {claim.figures.map((f, i) => {
            const { what, value } = figureLabel(f);
            return (
              <span
                key={i}
                className="rounded-sm border border-rule bg-white/60 px-1.5 font-mono text-[0.7rem]"
                title="Checked against SEC XBRL data"
              >
                {what} <strong className="font-medium">{value}</strong>
              </span>
            );
          })}
        </p>
      )}
    </div>
  );
}
