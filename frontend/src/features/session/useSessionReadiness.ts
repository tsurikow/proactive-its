import { useMemo } from "react";

import type { SessionReadiness } from "./types";
import { useReadinessQuery } from "./useTutorQueries";

export function useSessionReadiness(): SessionReadiness {
  const readinessQuery = useReadinessQuery();

  return useMemo<SessionReadiness>(() => {
    if (readinessQuery.isPending) {
      return {
        health: "checking",
        canInteract: false,
        detail: "Checking runtime readiness...",
        response: null,
      };
    }
    if (readinessQuery.error) {
      return {
        health: "down",
        canInteract: false,
        detail: "Backend is unavailable. Start the API and retry.",
        response: null,
      };
    }
    const response = readinessQuery.data ?? null;
    if (!response) {
      return {
        health: "down",
        canInteract: false,
        detail: "Backend is unavailable. Start the API and retry.",
        response: null,
      };
    }
    if (response.status === "ready") {
      return {
        health: "ready",
        canInteract: true,
        detail: `Runtime ready. Indexed sections: ${response.sections_count}, chunks: ${response.chunks_count}.`,
        response,
      };
    }
    const reasons: string[] = [];
    if (!response.database_ready) reasons.push("database not reachable");
    if (!response.template_ready) reasons.push("default template not seeded");
    if (!response.content_ready) reasons.push("content not indexed");
    return {
      health: "not_ready",
      canInteract: false,
      detail: `Runtime not ready: ${reasons.join(", ")}.`,
      response,
    };
  }, [readinessQuery.data, readinessQuery.error, readinessQuery.isPending]);
}
