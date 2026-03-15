import { useQueryClient } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";

import type { PlanProgress, StageInfo } from "../../types/api";
import { nextMessageId } from "../chat/message-utils";
import type { FeedMessage } from "../chat/types";
import { useNextSectionMutation, useStartSessionMutation } from "./useTutorMutations";
import type { PendingStatus } from "./types";

const LEARNER_STORAGE_KEY = "its.learner_id";

interface SessionBootstrapParams {
  learnerId: string | null;
  setLearnerId: (value: string) => void;
  setPlan: (value: PlanProgress | null) => void;
  setCurrentStage: (value: StageInfo | null) => void;
  setPlanCompleted: (value: boolean) => void;
  setMessages: Dispatch<SetStateAction<FeedMessage[]>>;
  setChatInput: (value: string) => void;
  setError: (value: { text: string; canRetry: boolean } | null) => void;
  setPendingStatus: (value: PendingStatus | null) => void;
  setFocusMessageId: (value: string | null) => void;
  runAction: (action: () => Promise<void>) => Promise<void>;
  tryLoadCurrentLesson: (activeLearner: string, version: number) => Promise<void>;
  refreshStartMessage: (activeLearner: string, messageId: string, version: number) => Promise<void>;
  beginRequestVersion: () => number;
  invalidateRequests: () => void;
  isActiveRequest: (activeLearner: string, version: number) => boolean;
}

export function useSessionBootstrap({
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
}: SessionBootstrapParams) {
  const queryClient = useQueryClient();
  const startMutation = useStartSessionMutation();
  const nextMutation = useNextSectionMutation();

  const start = async () => {
    if (!learnerId) {
      return;
    }
    await runAction(async () => {
      const version = beginRequestVersion();
      const response = await startMutation.mutateAsync(learnerId);
      if (!isActiveRequest(learnerId, version)) {
        return;
      }
      const introMessageId = nextMessageId();
      setPlan(response.plan);
      setCurrentStage(response.current_stage);
      setPlanCompleted(response.plan_completed);
      setMessages([
        {
          id: introMessageId,
          role: "system",
          title: "Tutor",
          content: response.message,
        },
      ]);
      void refreshStartMessage(learnerId, introMessageId, version);
      if (!response.plan_completed && response.current_stage) {
        await tryLoadCurrentLesson(learnerId, version);
      }
    });
  };

  const next = async () => {
    if (!learnerId) {
      return;
    }
    await runAction(async () => {
      const version = beginRequestVersion();
      const response = await nextMutation.mutateAsync({ learnerId, force: false });
      if (!isActiveRequest(learnerId, version)) {
        return;
      }
      setPlan(response.plan);
      setCurrentStage(response.current_stage);
      setPlanCompleted(response.plan_completed);
      setMessages((prev) => [
        ...prev,
        {
          id: nextMessageId(),
          role: "system",
          title: "Tutor",
          content: response.message,
        },
      ]);
      if (!response.plan_completed && response.current_stage) {
        await tryLoadCurrentLesson(learnerId, version);
      }
    });
  };

  const setLearner = (value: string) => {
    window.localStorage.setItem(LEARNER_STORAGE_KEY, value);
    invalidateRequests();
    queryClient.removeQueries({ queryKey: ["start-message"] });
    queryClient.removeQueries({ queryKey: ["lesson-current"] });
    setLearnerId(value);
    setPlan(null);
    setCurrentStage(null);
    setPlanCompleted(false);
    setMessages([]);
    setChatInput("");
    setPendingStatus(null);
    setFocusMessageId(null);
    setError(null);
  };

  return {
    start,
    next,
    setLearner,
  };
}
