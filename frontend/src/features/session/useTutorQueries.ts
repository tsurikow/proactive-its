import { useQuery } from "@tanstack/react-query";

import { getReadiness } from "../../api/client";
import { tutorQueryKeys } from "./queryKeys";

export function useReadinessQuery() {
  return useQuery({
    queryKey: tutorQueryKeys.readiness(),
    queryFn: getReadiness,
    retry: 1,
    staleTime: 15_000,
  });
}
