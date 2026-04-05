import type { AuthSessionState } from "../auth/useAuthSession";
import type { TutorSessionState } from "../session/useTutorSession";
import { ConversationPanel } from "./ConversationPanel";
import { WorkspaceTopbar } from "./WorkspaceTopbar";

export function TeacherWorkspace({
  session,
  auth,
}: {
  session: TutorSessionState;
  auth: Pick<AuthSessionState, "learner" | "logout" | "loading">;
}) {
  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[color:var(--bg-app)]">
      <WorkspaceTopbar status={session.status} actions={session.actions} auth={auth} />

      {session.status.health !== "ready" ? (
        <div className="px-3 pt-2 sm:px-4">
          <div className="teacher-banner teacher-banner-warning">{session.status.readiness.detail}</div>
        </div>
      ) : null}

      {session.status.error ? (
        <div className="px-3 pt-2 sm:px-4">
          <div className="teacher-banner teacher-banner-error">
            <span>{session.status.error.text}</span>
            {session.status.error.canRetry ? (
              <button
                type="button"
                onClick={() => void session.actions.retry()}
                disabled={session.status.loading}
                className="rounded-full bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:opacity-60"
              >
                Retry
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      <ConversationPanel
        status={session.status}
        transcript={session.transcript}
        composer={session.composer}
      />
    </div>
  );
}
