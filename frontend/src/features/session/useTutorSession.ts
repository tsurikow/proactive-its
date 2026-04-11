import { useEffect, useRef, useState } from "react";

import type { AuthLearner, TeacherAction } from "../../types/api";
import { normalizeApiError } from "./errors";
import { useRequestGuards } from "./requestGuards";
import type {
  ComposerState,
  PendingStatus,
  SessionActionState,
  SessionError,
  SessionReadiness,
  SessionStatusState,
  TranscriptState,
} from "./types";
import { useSessionProgress } from "./useSessionProgress";
import { useTranscript } from "./useTranscript";
import { useTeacherSessionMutation } from "./useTutorMutations";
import { useSessionReadiness } from "./useSessionReadiness";

export type { HealthState, SessionError } from "./types";

export interface TutorSessionState {
  status: SessionStatusState;
  transcript: TranscriptState;
  composer: ComposerState;
  actions: SessionActionState;
}

export function useTutorSession(authLearner: AuthLearner | null): TutorSessionState {
  const learnerId = authLearner?.id ?? null;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<SessionError | null>(null);
  const [chatInput, setChatInput] = useState("");
  const [pendingStatus, setPendingStatus] = useState<PendingStatus | null>(null);
  const retryActionRef = useRef<(() => Promise<void>) | null>(null);

  const sessionMutation = useTeacherSessionMutation();
  const readiness = useSessionReadiness();
  const progress = useSessionProgress(authLearner);
  const transcript = useTranscript();
  const { beginRequestVersion, invalidateRequests, isActiveRequest, currentRequestVersion } =
    useRequestGuards(learnerId);

  const hasLearner = Boolean(learnerId);
  const canInteract = hasLearner && readiness.canInteract;

  const runAction = async (action: () => Promise<void>) => {
    setLoading(true);
    setError(null);
    retryActionRef.current = null;
    try {
      await action();
    } catch (err) {
      const normalized = normalizeApiError(err);
      if (normalized.cancelled) {
        return;
      }
      retryActionRef.current = normalized.timeout ? action : null;
      setPendingStatus(null);
      setError({ text: normalized.message, canRetry: normalized.timeout });
      transcript.appendError(normalized.message);
    } finally {
      setLoading(false);
    }
  };

  const start = async () => {
    if (!learnerId || !canInteract) {
      return;
    }
    await runAction(async () => {
      const version = beginRequestVersion();
      setPendingStatus({ kind: "session", text: "Opening the teacher session..." });
      const response = await sessionMutation.mutateAsync({
        event_type: "open_session",
        force_move: false,
        context: {},
      });
      if (!isActiveRequest(learnerId, version)) {
        return;
      }
      setPendingStatus(null);
      progress.applyResult(response);
      transcript.appendTeacherTurn(response, { replaceMessages: true });
    });
  };

  const send = async () => {
    const message = chatInput.trim();
    if (!learnerId || !message || loading || !canInteract) {
      return;
    }

    setChatInput("");
    transcript.appendUserMessage(message);

    await runAction(async () => {
      const version = currentRequestVersion();
      setPendingStatus({
        kind: "reply",
        text: mandatoryTaskActive(progress.currentTeacherAction)
          ? "Teacher is checking your answer..."
          : "Teacher is thinking...",
      });
      const response = await sessionMutation.mutateAsync({
        event_type: "learner_reply",
        message,
        force_move: false,
        context: {
          current_module_id: progress.currentStage?.module_id ?? null,
          current_section_id: progress.currentStage?.section_id ?? null,
        },
      });
      if (!isActiveRequest(learnerId, version)) {
        return;
      }
      setPendingStatus(null);
      progress.applyResult(response);
      transcript.appendTeacherTurn(response);
    });
  };

  const retry = async () => {
    const action = retryActionRef.current;
    if (!action || loading) {
      return;
    }
    await runAction(action);
  };

  useEffect(() => {
    invalidateRequests();
    progress.reset();
    transcript.reset();
    setChatInput("");
    setPendingStatus(null);
    setError(null);
  }, [learnerId]);

  return {
    status: {
      learnerId,
      learnerEmail: authLearner?.email ?? null,
      hasLearner,
      learnerLabel: progress.learnerLabel,
      health: readiness.health,
      readiness,
      canInteract,
      loading,
      error,
      pendingStatus,
      focusMessageId: transcript.focusMessageId,
      planCompleted: progress.planCompleted,
      currentTitle: progress.currentTitle,
      currentBreadcrumb: progress.currentBreadcrumb,
      stageCountLabel: progress.stageCountLabel,
      progress: progress.progress,
      totalStages: progress.totalStages,
      completedStages: progress.completedStages,
      masteryScore: progress.masteryScore,
      masteryCaption: progress.masteryCaption,
      planTree: progress.planTree,
    },
    transcript: {
      messages: transcript.messages,
      focusMessageId: transcript.focusMessageId,
      clearFocusMessageId: transcript.clearFocusMessageId,
    },
    composer: {
      value: chatInput,
      setValue: setChatInput,
      send,
      disabled: loading || !hasLearner || !canInteract,
    },
    actions: {
      start,
      retry,
      canStart: !loading && hasLearner && canInteract,
    },
  };
}

function mandatoryTaskActive(action: TeacherAction | null): boolean {
  return (
    action?.action_type === "ask_section_question" ||
    action?.action_type === "assign_section_exercise" ||
    action?.action_type === "check_student_answer"
  );
}
