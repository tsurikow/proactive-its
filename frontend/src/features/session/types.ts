export type HealthState = "checking" | "ok" | "down";

export interface SessionError {
  text: string;
  canRetry: boolean;
}

export interface PendingStatus {
  kind: "lesson" | "answer";
  text: string;
}
