import { useQueryClient } from "@tanstack/react-query";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import type { PlanProgress, StageInfo } from "../../types/api";
import { nextMessageId, toLessonMessages } from "../chat/message-utils";
import type { FeedMessage } from "../chat/types";
import { normalizeApiError } from "./errors";
import { getCurrentLessonQueryOptions, getStartMessageQueryOptions } from "./useTutorQueries";
import type { PendingStatus, SessionError } from "./types";

interface LessonLoaderParams {
  setPlan: (value: PlanProgress | null) => void;
  setCurrentStage: (value: StageInfo | null) => void;
  setPlanCompleted: (value: boolean) => void;
  setMessages: Dispatch<SetStateAction<FeedMessage[]>>;
  setError: (value: SessionError | null) => void;
  setPendingStatus: (value: PendingStatus | null) => void;
  setFocusMessageId: (value: string | null) => void;
  retryActionRef: MutableRefObject<(() => Promise<void>) | null>;
  isActiveRequest: (learnerId: string, version: number) => boolean;
}

export function useLessonLoader({
  setPlan,
  setCurrentStage,
  setPlanCompleted,
  setMessages,
  setError,
  setPendingStatus,
  setFocusMessageId,
  retryActionRef,
  isActiveRequest,
}: LessonLoaderParams) {
  const queryClient = useQueryClient();

  const loadCurrentLesson = async (activeLearner: string, version: number) => {
    setPendingStatus({ kind: "lesson", text: "Generating lesson..." });
    const lessonResponse = await queryClient.fetchQuery(getCurrentLessonQueryOptions(activeLearner, version));
    if (!isActiveRequest(activeLearner, version)) {
      return;
    }
    setPendingStatus(null);
    setPlan(lessonResponse.plan);
    setCurrentStage(lessonResponse.current_stage);
    setPlanCompleted(lessonResponse.plan_completed);
    if (!lessonResponse.lesson) {
      return;
    }
    const lessonMessages = toLessonMessages(lessonResponse.lesson);
    if (lessonMessages[0]) {
      setFocusMessageId(lessonMessages[0].id);
    }
    setMessages((prev) => [...prev, ...lessonMessages]);
  };

  const tryLoadCurrentLesson = async (activeLearner: string, version: number) => {
    try {
      await loadCurrentLesson(activeLearner, version);
    } catch (err) {
      if (!isActiveRequest(activeLearner, version)) {
        return;
      }
      setPendingStatus(null);
      const normalized = normalizeApiError(err);
      retryActionRef.current = normalized.timeout
        ? async () => {
            await loadCurrentLesson(activeLearner, version);
          }
        : null;
      setError({ text: normalized.message, canRetry: normalized.timeout });
      setMessages((prev) => [
        ...prev,
        {
          id: nextMessageId(),
          role: "error",
          kind: "error",
          title: "Lesson fetch failed",
          content: normalized.message,
        },
      ]);
    }
  };

  const refreshStartMessage = async (activeLearner: string, messageId: string, version: number) => {
    try {
      const response = await queryClient.fetchQuery(getStartMessageQueryOptions(activeLearner, version));
      if (!isActiveRequest(activeLearner, version) || !response.message.trim()) {
        return;
      }
      setMessages((prev) =>
        prev.map((message) =>
          message.id === messageId && message.role === "system"
            ? { ...message, content: response.message }
            : message,
        ),
      );
    } catch {
      // Keep the deterministic start message if the richer greeting fails.
    }
  };

  return {
    loadCurrentLesson,
    tryLoadCurrentLesson,
    refreshStartMessage,
  };
}
