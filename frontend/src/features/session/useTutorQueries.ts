import { useQuery } from "@tanstack/react-query";

import { getCurrentLesson, getHealth, getStartMessage } from "../../api/client";
import { tutorQueryKeys } from "./queryKeys";

export function useHealthQuery() {
  return useQuery({
    queryKey: tutorQueryKeys.health(),
    queryFn: getHealth,
    retry: 1,
    staleTime: 15_000,
  });
}

export function getStartMessageQueryOptions(learnerId: string, version: number) {
  return {
    queryKey: tutorQueryKeys.startMessage(learnerId, version),
    queryFn: () => getStartMessage(learnerId),
    staleTime: 60_000,
  } as const;
}

export function getCurrentLessonQueryOptions(learnerId: string, version: number) {
  return {
    queryKey: tutorQueryKeys.currentLesson(learnerId, version),
    queryFn: () => getCurrentLesson(learnerId),
    staleTime: 30_000,
  } as const;
}
