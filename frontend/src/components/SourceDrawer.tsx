"use client";

import { useEffect, useRef } from "react";

import { SECTION_LABELS, fiscalYear } from "@/lib/format";
import type { RetrievedChunk } from "@/lib/types";

export function SourceDrawer({
  number,
  source,
  onClose,
}: {
  number: number;
  source: RetrievedChunk;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const c = source.citation;
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="presentation">
      <button
        aria-label="Close source"
        className="absolute inset-0 bg-ink/20"
        onClick={onClose}
        tabIndex={-1}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-title"
        className="relative flex h-full w-full max-w-lg flex-col border-l border-rule bg-sheet shadow-xl"
      >
        <header className="flex items-start gap-3 border-b border-rule px-5 py-4">
          <div>
            <p className="font-mono text-xs text-pencil">Source {number}</p>
            <h2
              id="source-title"
              className="display text-xl font-bold uppercase"
            >
              {c.ticker} {c.form}{" "}
              {c.report_date ? fiscalYear(c.report_date) : ""}
            </h2>
            <p className="font-mono text-xs text-graphite">
              {SECTION_LABELS[c.section] ?? c.section} · passage #{c.chunk_id}
            </p>
          </div>
          <button
            ref={closeRef}
            onClick={onClose}
            className="display ml-auto text-sm font-semibold uppercase hover:text-pencil"
          >
            Close
          </button>
        </header>
        <blockquote className="flex-1 overflow-y-auto whitespace-pre-line px-5 py-4 text-[0.95rem] leading-relaxed">
          {source.text}
        </blockquote>
        <footer className="border-t border-rule px-5 py-3">
          <a
            href={c.url}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-xs underline decoration-rule underline-offset-4 hover:text-pencil"
          >
            Open the filing on SEC EDGAR ↗
          </a>
        </footer>
      </aside>
    </div>
  );
}
