import type { Citation } from "../../types/api";

export function CitationList({ citations }: { citations: Citation[] }) {
  return (
    <div className="mt-3 rounded-[var(--radius-md)] border border-[color:var(--line-soft)] bg-[color:var(--surface-soft)] px-4 py-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-[color:var(--ink-soft)]">
        Sources
      </div>
      <div className="mt-2 space-y-2">
        {citations.map((citation) => (
          <div
            key={citation.chunk_id}
            className="rounded-[var(--radius-sm)] bg-[color:var(--surface)] px-3 py-2.5 shadow-[var(--shadow-hairline)]"
          >
            <div className="text-sm font-semibold text-[color:var(--ink-strong)]">{citation.title}</div>
            <div className="mt-0.5 text-xs text-[color:var(--ink-soft)]">
              {citation.breadcrumb.join(" → ")}
            </div>
            <div className="mt-1.5 text-sm italic text-[color:var(--ink-muted)]">"{citation.quote}"</div>
          </div>
        ))}
      </div>
    </div>
  );
}
