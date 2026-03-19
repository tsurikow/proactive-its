import {
  Activity,
  ChevronRight,
  PlayCircle,
  Sparkles,
  User,
} from "lucide-react";

import type { HealthState } from "../session/types";

interface HeaderBarProps {
  health: HealthState;
  learnerLabel: string;
  stageCountLabel: string;
  loading: boolean;
  hasLearner: boolean;
  planCompleted: boolean;
  onStart: () => void;
  onNext: () => void;
}

export function HeaderBar({
  health,
  learnerLabel,
  stageCountLabel,
  loading,
  hasLearner,
  planCompleted,
  onStart,
  onNext,
}: HeaderBarProps) {
  return (
    <header className="sticky top-0 z-40 flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 shadow-sm sm:px-6">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <div className="rounded-lg bg-teal-600 p-1.5">
            <Sparkles className="h-5 w-5 text-white" />
          </div>
          <h1 className="hidden text-xl font-bold tracking-tight text-slate-800 sm:block">
            Proactive Calculus
          </h1>
        </div>

        <div className="hidden items-center gap-3 text-sm font-medium md:flex">
          <div
            data-testid="health-indicator"
            className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${
              health === "ok"
                ? "border-emerald-100 bg-emerald-50 text-emerald-700"
                : health === "checking"
                  ? "border-amber-100 bg-amber-50 text-amber-700"
                  : "border-rose-100 bg-rose-50 text-rose-700"
            }`}
          >
            <span className="h-2 w-2 animate-pulse rounded-full bg-current" />
            {health === "ok" ? "API Online" : health === "checking" ? "Checking API" : "API Offline"}
          </div>
          <div
            data-testid="header-learner-label"
            className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-100 px-2.5 py-1 text-slate-600"
          >
            <User className="h-3.5 w-3.5" />
            {learnerLabel}
          </div>
          <div
            data-testid="header-stage-count"
            className="flex items-center gap-1.5 rounded-full border border-indigo-100 bg-indigo-50 px-2.5 py-1 text-indigo-700"
          >
            <Activity className="h-3.5 w-3.5" />
            {stageCountLabel}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          data-testid="start-session-button"
          onClick={onStart}
          disabled={loading || !hasLearner}
          className="hidden items-center gap-2 rounded-xl bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-60 sm:flex"
        >
          <PlayCircle className="h-4 w-4" />
          Start Session
        </button>
        <button
          data-testid="next-section-button"
          onClick={onNext}
          disabled={loading || !hasLearner || planCompleted}
          className="flex items-center gap-2 rounded-xl bg-teal-600 px-4 py-2 text-sm font-semibold text-white shadow-sm shadow-teal-600/20 transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Next Section
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}
