import { LearnerGate } from "./components/LearnerGate";
import { ChatPane } from "./features/chat/ChatPane";
import { HeaderBar } from "./features/layout/HeaderBar";
import { PlanSidebar } from "./features/plan/PlanSidebar";
import { useTutorSession } from "./features/session/useTutorSession";

export default function App() {
  const session = useTutorSession();

  return (
    <div className="min-h-screen overflow-hidden bg-[#F8FAFC] font-sans text-slate-900">
      {!session.hasLearner ? <LearnerGate onSubmit={session.setLearner} /> : null}

      <div className="flex h-dvh flex-col">
        <HeaderBar
          health={session.health}
          learnerLabel={session.learnerLabel}
          stageCountLabel={session.stageCountLabel}
          loading={session.loading}
          hasLearner={session.hasLearner}
          planCompleted={session.planCompleted}
          onStart={() => void session.start()}
          onNext={() => void session.next()}
        />

        {session.error ? (
          <div
            data-testid="session-error"
            className="mx-4 mt-2 flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 sm:mx-6"
          >
            <span>{session.error.text}</span>
            {session.error.canRetry ? (
              <button
                onClick={() => void session.retry()}
                disabled={session.loading}
                className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-700 disabled:opacity-60"
              >
                Retry
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="mx-auto flex w-full max-w-[1600px] min-h-0 flex-1 overflow-hidden">
          <ChatPane
            currentTitle={session.currentTitle}
            currentBreadcrumb={session.currentBreadcrumb}
            messages={session.messages}
            loading={session.loading}
            pendingStatus={session.pendingStatus}
            focusMessageId={session.focusMessageId}
            onFocusedMessage={session.clearFocusMessageId}
            hasLearner={session.hasLearner}
            chatInput={session.chatInput}
            onChatInputChange={session.setChatInput}
            onSend={session.send}
          />
          <PlanSidebar
            completedStages={session.completedStages}
            totalStages={session.totalStages}
            progress={session.progress}
            masteryScore={session.subjectMasteryScore}
            tree={session.planTree}
            planCompleted={session.planCompleted}
          />
        </div>
      </div>
    </div>
  );
}
