import { BookCopy, LogOut, PlayCircle, Sparkles, UserRound } from "lucide-react";

import type { AuthSessionState } from "../auth/useAuthSession";
import type { SessionActionState, SessionStatusState } from "../session/types";

interface WorkspaceTopbarProps {
  status: SessionStatusState;
  actions: SessionActionState;
  auth: Pick<AuthSessionState, "learner" | "logout" | "loading">;
}

export function WorkspaceTopbar({ status, actions, auth }: WorkspaceTopbarProps) {
  const statusTone =
    status.health === "ready"
      ? "text-emerald-700 bg-emerald-50 border-emerald-200"
      : status.health === "checking"
        ? "text-amber-700 bg-amber-50 border-amber-200"
        : "text-rose-700 bg-rose-50 border-rose-200";

  return (
    <header className="teacher-topbar">
      <div className="flex min-w-0 items-center gap-2.5">
        <div className="teacher-brand-mark">
          <Sparkles className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[color:var(--ink-strong)] sm:text-base">
            {status.currentTitle}
          </div>
          <div className="hidden truncate text-xs text-[color:var(--ink-muted)] sm:block">
            {status.currentBreadcrumb}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1.5 sm:gap-2">
        <div className={`hidden rounded-full border px-2.5 py-1 text-xs font-semibold lg:inline-flex ${statusTone}`}>
          {status.health === "ready" ? "Ready" : status.health === "checking" ? "Checking" : "Needs attention"}
        </div>
        <div className="hidden items-center gap-1.5 rounded-full border border-[color:var(--line-soft)] bg-[color:var(--surface)] px-2.5 py-1 text-xs font-medium text-[color:var(--ink-muted)] xl:inline-flex">
          <UserRound className="h-3.5 w-3.5" />
          {status.learnerLabel}
        </div>
        <details className="hidden lg:block">
          <summary className="cursor-pointer list-none rounded-full border border-[color:var(--line-soft)] bg-[color:var(--surface)] px-2.5 py-1 text-xs font-medium text-[color:var(--ink-muted)]">
            <span className="inline-flex items-center gap-1.5">
              <BookCopy className="h-3.5 w-3.5" />
              {status.stageCountLabel}
            </span>
          </summary>
          <div className="teacher-progress-popover">
            <div className="text-sm font-semibold text-[color:var(--ink-strong)]">{status.currentTitle}</div>
            <div className="mt-1 text-sm text-[color:var(--ink-muted)]">{status.currentBreadcrumb}</div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[color:var(--surface-soft)]">
              <div
                className="h-full rounded-full bg-[color:var(--accent-strong)]"
                style={{ width: `${status.progress}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-[color:var(--ink-soft)]">
              <span>{status.stageCountLabel}</span>
              <span>{status.masteryCaption}</span>
            </div>
          </div>
        </details>
        <button
          type="button"
          onClick={() => void actions.start()}
          disabled={!actions.canStart}
          className="inline-flex h-11 min-w-11 items-center justify-center gap-1.5 rounded-full border border-[color:var(--line-soft)] bg-[color:var(--surface)] px-3 text-sm font-semibold text-[color:var(--ink-strong)] transition hover:bg-[color:var(--surface-soft)] disabled:cursor-not-allowed disabled:opacity-50 sm:px-4"
        >
          <PlayCircle className="h-4 w-4" />
          <span className="hidden sm:inline">Start</span>
        </button>
        <button
          type="button"
          onClick={() => void auth.logout()}
          disabled={auth.loading}
          className="inline-flex h-11 min-w-11 items-center justify-center gap-1.5 rounded-full border border-[color:var(--line-soft)] bg-[color:var(--surface)] px-3 text-sm font-semibold text-[color:var(--ink-strong)] transition hover:bg-[color:var(--surface-soft)] disabled:opacity-60 sm:px-4"
        >
          <LogOut className="h-4 w-4" />
          <span className="hidden sm:inline">Sign out</span>
        </button>
      </div>
    </header>
  );
}
