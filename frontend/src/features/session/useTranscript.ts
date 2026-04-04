import { useState } from "react";

import type { TeacherSessionResult } from "../../types/api";
import { nextMessageId, toLessonMessages, type FeedMessage } from "./messages";

export interface TranscriptActions {
  appendUserMessage: (content: string) => void;
  appendTeacherTurn: (result: TeacherSessionResult, options?: { replaceMessages?: boolean }) => void;
  appendError: (message: string) => void;
  reset: () => void;
  focusMessageId: string | null;
  clearFocusMessageId: () => void;
  messages: FeedMessage[];
}

export function useTranscript(): TranscriptActions {
  const [messages, setMessages] = useState<FeedMessage[]>([]);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);

  const appendUserMessage = (content: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: nextMessageId(),
        role: "user",
        kind: "chat",
        content,
      },
    ]);
  };

  const appendTeacherTurn = (
    result: TeacherSessionResult,
    options?: { replaceMessages?: boolean },
  ) => {
    const replaceMessages = Boolean(options?.replaceMessages);
    const additions: FeedMessage[] = [];

    if (result.teacher_message.trim()) {
      additions.push({
        id: nextMessageId(),
        role: "assistant",
        kind: "chat",
        title: "Teacher",
        content: result.teacher_message,
        citations: result.citations ?? [],
        interactionId: result.interaction_id ?? undefined,
        checkpointEvaluation: result.checkpoint_evaluation ?? null,
      });
    }

    additions.push(...toLessonMessages(result.lesson ?? null));

    for (let index = additions.length - 1; index >= 0; index -= 1) {
      const candidate = additions[index];
      if (candidate.role === "assistant" && candidate.kind !== "system" && candidate.kind !== "error") {
        setFocusMessageId(candidate.id);
        break;
      }
    }

    if (!additions.length) {
      return;
    }
    if (replaceMessages) {
      setMessages(additions);
      return;
    }
    setMessages((prev) => [...prev, ...additions]);
  };

  const appendError = (message: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: nextMessageId(),
        role: "error",
        kind: "error",
        title: "Request failed",
        content: message,
      },
    ]);
  };

  const reset = () => {
    setMessages([]);
    setFocusMessageId(null);
  };

  return {
    messages,
    focusMessageId,
    clearFocusMessageId: () => setFocusMessageId(null),
    appendUserMessage,
    appendTeacherTurn,
    appendError,
    reset,
  };
}
