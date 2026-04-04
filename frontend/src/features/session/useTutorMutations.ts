import { useMutation } from "@tanstack/react-query";

import { runTeacherSession } from "../../api/client";
import type { TeacherSessionRequest } from "../../types/api";

export function useTeacherSessionMutation() {
  return useMutation({
    mutationFn: (request: TeacherSessionRequest) => runTeacherSession(request),
  });
}
