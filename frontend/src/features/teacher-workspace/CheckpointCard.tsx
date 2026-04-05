import type { CheckpointEvaluation } from "../../types/api";

export function CheckpointCard({ evaluation }: { evaluation: CheckpointEvaluation }) {
  return (
    <div className="mt-3 rounded-[var(--radius-md)] border border-amber-200 bg-amber-50 px-4 py-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-amber-700">
        Answer check
      </div>
      <div className="mt-2 text-sm font-semibold text-amber-950">
        {statusLabel(evaluation.status ?? "")}
      </div>
      <div className="mt-1 text-sm text-amber-900">{evaluation.rationale}</div>
    </div>
  );
}

function statusLabel(status: string): string {
  switch (status) {
    case "correct":
      return "Correct";
    case "partial":
      return "Partially correct";
    case "incorrect":
      return "Not correct yet";
    case "skipped":
      return "Skipped";
    default:
      return "Unresolved";
  }
}
