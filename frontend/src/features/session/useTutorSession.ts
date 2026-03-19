import { useMemo, useRef, useState } from "react";

import type { PlanProgress, StageInfo } from "../../types/api";
import type { FeedMessage } from "../chat/types";
import { nextMessageId } from "../chat/message-utils";
import { normalizeApiError } from "./errors";
import { useChatSession } from "./useChatSession";
import { useLessonLoader } from "./useLessonLoader";
import { useRequestGuards } from "./requestGuards";
import { useSessionBootstrap } from "./useSessionBootstrap";
import { useHealthQuery } from "./useTutorQueries";
import type { HealthState, PendingStatus, SessionError } from "./types";

const LEARNER_STORAGE_KEY = "its.learner_id";

export type { HealthState, SessionError } from "./types";

export function useTutorSession() {
  const [learnerId, setLearnerId] = useState<string | null>(() =>
    window.localStorage.getItem(LEARNER_STORAGE_KEY),
  );
  const [plan, setPlan] = useState<PlanProgress | null>(null);
  const [currentStage, setCurrentStage] = useState<StageInfo | null>(null);
  const [planCompleted, setPlanCompleted] = useState(false);
  const [messages, setMessages] = useState<FeedMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<SessionError | null>(null);
  const [chatInput, setChatInput] = useState("");
  const [pendingStatus, setPendingStatus] = useState<PendingStatus | null>(null);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);
  const retryActionRef = useRef<(() => Promise<void>) | null>(null);
  const healthQuery = useHealthQuery();

  const { beginRequestVersion, invalidateRequests, isActiveRequest, currentRequestVersion } =
    useRequestGuards(learnerId);

  const health: HealthState =
    healthQuery.isPending ? "checking" : healthQuery.data?.status === "ok" ? "ok" : "down";

  const hasLearner = Boolean(learnerId);

  const learnerLabel = useMemo(
    () => (learnerId ? `Learner: ${learnerId}` : "No learner"),
    [learnerId],
  );

  const stageCountLabel = useMemo(() => {
    if (!plan?.total_stages) {
      return "Stage -/-";
    }
    const stageValue = currentStage
      ? currentStage.stage_index + 1
      : Math.min(plan.completed_stages, plan.total_stages);
    return `Stage ${stageValue}/${plan.total_stages}`;
  }, [plan, currentStage]);

  const progress = useMemo(() => {
    if (!plan?.total_stages) {
      return 0;
    }
    return Math.min(100, Math.round((plan.completed_stages / plan.total_stages) * 100));
  }, [plan]);

  const currentTitle = currentStage?.title || currentStage?.section_id || "No active section";
  const currentBreadcrumb = currentStage?.breadcrumb?.join(" -> ") || "Start session to load stage";
  const currentNumber = currentStage ? currentStage.stage_index + 1 : 0;
  const totalStages = plan?.total_stages ?? 0;
  const completedStages = plan?.completed_stages ?? 0;
  const nextStage = currentStage && totalStages > currentNumber ? currentNumber + 1 : null;
  const planTree = plan?.tree ?? null;
  const subjectMasteryScore = plan?.mastery_score ?? 0;

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
      setMessages((prev) => [
        ...prev,
        {
          id: nextMessageId(),
          role: "error",
          kind: "error",
          title: "Request failed",
          content: normalized.message,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const { loadCurrentLesson, tryLoadCurrentLesson, refreshStartMessage } = useLessonLoader({
    setPlan,
    setCurrentStage,
    setPlanCompleted,
    setMessages,
    setError,
    setPendingStatus,
    setFocusMessageId,
    retryActionRef,
    isActiveRequest,
  });

  const { start, next, setLearner } = useSessionBootstrap({
    learnerId,
    setLearnerId,
    setPlan,
    setCurrentStage,
    setPlanCompleted,
    setMessages,
    setChatInput,
    setError,
    setPendingStatus,
    setFocusMessageId,
    runAction,
    tryLoadCurrentLesson,
    refreshStartMessage,
    beginRequestVersion,
    invalidateRequests,
    isActiveRequest,
  });

  const { send } = useChatSession({
    learnerId,
    loading,
    chatInput,
    currentStage,
    setChatInput,
    setMessages,
    setPendingStatus,
    setFocusMessageId,
    runAction,
    isActiveRequest,
    currentRequestVersion,
  });

  const retry = async () => {
    const action = retryActionRef.current;
    if (!action || loading) {
      return;
    }
    await runAction(action);
  };

  return {
    learnerId,
    hasLearner,
    learnerLabel,
    stageCountLabel,
    health,
    loading,
    error,
    chatInput,
    setChatInput,
    messages,
    currentTitle,
    currentBreadcrumb,
    currentNumber,
    totalStages,
    completedStages,
    nextStage,
    progress,
    planTree,
    subjectMasteryScore,
    planCompleted,
    pendingStatus,
    focusMessageId,
    clearFocusMessageId: () => setFocusMessageId(null),
    start,
    next,
    send,
    retry,
    setLearner,
    loadCurrentLesson,
  };
}
