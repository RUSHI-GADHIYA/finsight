/** An auditor's red-pencil tick: the checker verified this claim against its sources. */
export function TickMark({ delayMs = 0 }: { delayMs?: number }) {
  return (
    <svg
      viewBox="0 0 24 20"
      className="tick h-5 w-6 text-pencil"
      style={{ "--tick-delay": `${delayMs}ms` } as React.CSSProperties}
      role="img"
      aria-label="Verified against its sources"
    >
      <path
        d="M2.5 10.5 C4.5 12 6.5 14.5 8.5 17.5 C11 11 15.5 5.5 21.5 2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** The checker could not verify this claim; the reviewer should look closely. */
export function FlagMark({ reason }: { reason?: string }) {
  return (
    <span
      className="display inline-flex h-5 w-6 items-center justify-center rounded-full border-2 border-pencil text-xs font-bold text-pencil"
      role="img"
      aria-label={reason ? `Not verified: ${reason}` : "Not verified"}
      title={reason}
    >
      ?
    </span>
  );
}
