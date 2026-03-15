import type { Dispatch, SetStateAction } from "react";

import type { StageInfo } from "../../types/api";
import { nextMessageId } from "../chat/message-utils";
import type { FeedMessage } from "../chat/types";
import { useChatMutation } from "./useTutorMutations";
import type { PendingStatus } from "./types";

interface ChatSessionParams {
  learnerId: string | null;
  loading: boolean;
  chatInput: string;
  currentStage: StageInfo | null;
  setChatInput: (value: string) => void;
  setMessages: Dispatch<SetStateAction<FeedMessage[]>>;
  setPendingStatus: (value: PendingStatus | null) => void;
  setFocusMessageId: (value: string | null) => void;
  runAction: (action: () => Promise<void>) => Promise<void>;
  isActiveRequest: (learnerId: string, version: number) => boolean;
  currentRequestVersion: () => number;
}

export function useChatSession({
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
}: ChatSessionParams) {
  const chatMutation = useChatMutation();

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
      const version = currentRequestVersion();
      setPendingStatus({ kind: "answer", text: "Tutor is preparing an answer..." });
      const response = await chatMutation.mutateAsync({
        learnerId,
        message,
        moduleId: currentStage?.module_id ?? null,
        sectionId: currentStage?.section_id ?? null,
      });
      if (!isActiveRequest(learnerId, version)) {
        return;
      }
      setPendingStatus(null);
      const answerId = nextMessageId();
      setFocusMessageId(answerId);
      setMessages((prev) => [
        ...prev,
        {
          id: answerId,
          role: "assistant",
          title: "Answer",
          content: response.answer_md,
          citations: response.citations,
        },
      ]);
    });
  };

  return { send };
}
