import { Activity, CheckCircle2 } from "lucide-react";

interface PlanSidebarProps {
  completedStages: number;
  totalStages: number;
  progress: number;
  currentNumber: number;
  currentTitle: string;
  currentBreadcrumb: string;
  nextStage: number | null;
  planCompleted: boolean;
}

export function PlanSidebar({
  completedStages,
  totalStages,
  progress,
  currentNumber,
  currentTitle,
  currentBreadcrumb,
  nextStage,
  planCompleted,
}: PlanSidebarProps) {
  return (
    <aside className="hidden w-80 flex-col border-l border-slate-200 bg-white lg:flex">
      <div className="flex-1 overflow-y-auto p-6">
        <h3 className="mb-6 flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-slate-900">
          <Activity className="h-4 w-4 text-teal-600" />
          Plan Progress
        </h3>

        <div className="mb-8 rounded-2xl border border-slate-200 bg-slate-50 p-5">
          <div className="mb-3 flex items-end justify-between">
            <span className="text-sm font-semibold text-slate-700">Completed</span>
            <span className="text-xl font-bold text-slate-900">
              {completedStages}
              <span className="text-sm font-medium text-slate-500">/{totalStages || "-"}</span>
            </span>
          </div>
          <div className="mb-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-200">
            <div className="h-2.5 rounded-full bg-teal-500" style={{ width: `${progress}%` }} />
          </div>
          <p className="text-right text-xs font-medium text-slate-500">{progress}% done</p>
        </div>

        <div className="relative">
          <div className="absolute bottom-0 left-4 top-8 w-0.5 bg-slate-100" />

          <div className="relative mb-6 flex gap-4">
            <div className="z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-white bg-teal-100 text-teal-600">
              <CheckCircle2 className="h-5 w-5" />
            </div>
            <div>
              <h4 className="text-sm font-medium text-slate-500 line-through">
                {completedStages > 0 ? `${completedStages} stages completed` : "No completed stages yet"}
              </h4>
            </div>
          </div>

          <div className="relative mb-6 flex gap-4">
            <div className="z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-white bg-teal-600 shadow-sm">
              <div className="h-2.5 w-2.5 animate-pulse rounded-full bg-white" />
            </div>
            <div className="flex-1 rounded-xl border-2 border-teal-500 bg-white p-4 shadow-sm shadow-teal-500/10">
              <div className="mb-1 text-xs font-bold uppercase tracking-wider text-teal-600">Current Stage</div>
              <h4 className="mb-2 text-sm font-bold text-slate-900">
                {currentNumber > 0 ? `Stage ${currentNumber}: ` : ""}
                {currentTitle}
              </h4>
              <p className="mb-3 text-xs leading-relaxed text-slate-500">{currentBreadcrumb}</p>
            </div>
          </div>

          <div className="relative flex gap-4">
            <div className="z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-white bg-slate-100 text-slate-400">
              <span className="text-xs font-bold">{nextStage ?? "-"}</span>
            </div>
            <div className="flex-1 pt-1.5">
              <h4 className="text-sm font-medium text-slate-600">Next action</h4>
              <p className="mt-1 text-xs text-slate-500">
                {planCompleted
                  ? "Plan completed. Review previous sections."
                  : "Continue current stage or move to next section."}
              </p>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}
