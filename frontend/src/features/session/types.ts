import type { PlanTreeNode, ReadinessResponse } from "../../types/api";
import type { FeedMessage } from "./messages";

export type HealthState = "checking" | "ready" | "not_ready" | "down";

export interface SessionError {
  text: string;
  canRetry: boolean;
}

export interface PendingStatus {
  kind: "session" | "reply";
  text: string;
}

export interface SessionReadiness {
  health: HealthState;
  canInteract: boolean;
  detail: string;
  response: ReadinessResponse | null;
}

export interface SessionStatusState {
  learnerId: string | null;
  learnerEmail: string | null;
  hasLearner: boolean;
  learnerLabel: string;
  health: HealthState;
  readiness: SessionReadiness;
  canInteract: boolean;
  loading: boolean;
  error: SessionError | null;
  pendingStatus: PendingStatus | null;
  focusMessageId: string | null;
  planCompleted: boolean;
  currentTitle: string;
  currentBreadcrumb: string;
  stageCountLabel: string;
  progress: number;
  totalStages: number;
  completedStages: number;
  masteryScore: number;
  masteryCaption: string;
  planTree: PlanTreeNode | null;
}

export interface TranscriptState {
  messages: FeedMessage[];
  focusMessageId: string | null;
  clearFocusMessageId: () => void;
}

export interface ComposerState {
  value: string;
  setValue: (value: string) => void;
  send: () => Promise<void>;
  disabled: boolean;
}

export interface SessionActionState {
  start: () => Promise<void>;
  retry: () => Promise<void>;
  canStart: boolean;
}
