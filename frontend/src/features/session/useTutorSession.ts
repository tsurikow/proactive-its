import { useEffect, useMemo, useRef, useState } from "react";

import { getCurrentLesson, getHealth, nextSection, sendChat, startSession } from "../../api/client";
import type { PlanProgress, StageInfo } from "../../types/api";
import { nextMessageId, toLessonMessages } from "../chat/message-utils";
import type { FeedMessage } from "../chat/types";
import { normalizeApiError } from "./errors";

const LEARNER_STORAGE_KEY = "its.learner_id";

export type HealthState = "checking" | "ok" | "down";

export interface SessionError {
  text: string;
  canRetry: boolean;
}

export function useTutorSession() {
  const [learnerId, setLearnerId] = useState<string | null>(() =>
    window.localStorage.getItem(LEARNER_STORAGE_KEY),
  );
  const [plan, setPlan] = useState<PlanProgress | null>(null);
  const [currentStage, setCurrentStage] = useState<StageInfo | null>(null);
  const [planCompleted, setPlanCompleted] = useState(false);
  const [messages, setMessages] = useState<FeedMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState<HealthState>("checking");
  const [error, setError] = useState<SessionError | null>(null);
  const [chatInput, setChatInput] = useState("");
  const retryActionRef = useRef<(() => Promise<void>) | null>(null);

  useEffect(() => {
    const check = async () => {
      try {
        const response = await getHealth();
        setHealth(response.status === "ok" ? "ok" : "down");
      } catch {
        setHealth("down");
      }
    };
    void check();
  }, []);

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
      setError({ text: normalized.message, canRetry: normalized.timeout });
      setMessages((prev) => [
        ...prev,
        {
          id: nextMessageId(),
          role: "error",
          title: "Request failed",
          content: normalized.message,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const loadCurrentLesson = async (activeLearner: string) => {
    const lessonResponse = await getCurrentLesson(activeLearner);
    setCurrentStage(lessonResponse.current_stage);
    setPlanCompleted(lessonResponse.plan_completed);
    if (!lessonResponse.lesson) {
      return;
    }
    setMessages((prev) => [...prev, ...toLessonMessages(lessonResponse.lesson)]);
  };

  const tryLoadCurrentLesson = async (activeLearner: string) => {
    try {
      await loadCurrentLesson(activeLearner);
    } catch (err) {
      const normalized = normalizeApiError(err);
      retryActionRef.current = normalized.timeout
        ? async () => {
            await loadCurrentLesson(activeLearner);
          }
        : null;
      setError({ text: normalized.message, canRetry: normalized.timeout });
      setMessages((prev) => [
        ...prev,
        {
          id: nextMessageId(),
          role: "error",
          title: "Lesson fetch failed",
          content: normalized.message,
        },
      ]);
    }
  };

  const start = async () => {
    if (!learnerId) {
      return;
    }
    await runAction(async () => {
      const response = await startSession(learnerId);
      setPlan(response.plan);
      setCurrentStage(response.current_stage);
      setPlanCompleted(response.plan_completed);
      setMessages([
        {
          id: nextMessageId(),
          role: "system",
          title: "Tutor",
          content: response.message,
        },
      ]);
      if (!response.plan_completed && response.current_stage) {
        await tryLoadCurrentLesson(learnerId);
      }
    });
  };

  const next = async () => {
    if (!learnerId) {
      return;
    }
    await runAction(async () => {
      const response = await nextSection(learnerId, false);
      setCurrentStage(response.current_stage);
      setPlanCompleted(response.plan_completed);
      setPlan((prev) =>
        prev
          ? {
              ...prev,
              completed_stages: Math.min(prev.total_stages, prev.completed_stages + 1),
            }
          : prev,
      );

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
        await tryLoadCurrentLesson(learnerId);
      }
    });
  };

  const send = async () => {
    const message = chatInput.trim();
    if (!learnerId || !message || loading) {
      return;
    }

    setChatInput("");
    setMessages((prev) => [
      ...prev,
      {
        id: nextMessageId(),
        role: "user",
        content: message,
      },
    ]);

    await runAction(async () => {
      const response = await sendChat(
        learnerId,
        message,
        currentStage?.module_id ?? null,
        currentStage?.section_id ?? null,
      );
      setMessages((prev) => [
        ...prev,
        {
          id: nextMessageId(),
          role: "assistant",
          title: "Answer",
          content: response.answer_md,
          citations: response.citations,
        },
      ]);
    });
  };

  const retry = async () => {
    const action = retryActionRef.current;
    if (!action || loading) {
      return;
    }
    await runAction(action);
  };

  const setLearner = (value: string) => {
    window.localStorage.setItem(LEARNER_STORAGE_KEY, value);
    setLearnerId(value);
    setError(null);
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
    planCompleted,
    start,
    next,
    send,
    retry,
    setLearner,
  };
}
