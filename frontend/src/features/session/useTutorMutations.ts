import { useMutation } from "@tanstack/react-query";

import { nextSection, sendChat, startSession } from "../../api/client";

export function useStartSessionMutation() {
  return useMutation({
    mutationFn: (learnerId: string) => startSession(learnerId),
  });
}

export function useNextSectionMutation() {
  return useMutation({
    mutationFn: ({ learnerId, force }: { learnerId: string; force: boolean }) => nextSection(learnerId, force),
  });
}

export function useChatMutation() {
  return useMutation({
    mutationFn: ({
      learnerId,
      message,
      moduleId,
      sectionId,
    }: {
      learnerId: string;
      message: string;
      moduleId: string | null;
      sectionId: string | null;
    }) => sendChat(learnerId, message, moduleId, sectionId),
  });
}
