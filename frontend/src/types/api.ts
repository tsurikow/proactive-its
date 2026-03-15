import type { components } from "./generated/api";

export type LessonStep = components["schemas"]["LessonStep"];
export type StageInfo = components["schemas"]["StageInfo"];
export type PlanTreeNode = components["schemas"]["PlanTreeNode"];
export type PlanProgress = components["schemas"]["PlanProgress"];
export type LessonPayload = components["schemas"]["LessonPayload"];
export type StartResponse = components["schemas"]["StartResponse"];
export type StartMessageResponse = components["schemas"]["StartMessageResponse"];
export type LessonCurrentResponse = components["schemas"]["LessonCurrentResponse"];
export type NextResponse = components["schemas"]["NextResponse"];
export type Citation = components["schemas"]["Citation"];
export type RetrievalDebug = components["schemas"]["RetrievalDebug"];
export type ChatResponse = components["schemas"]["ChatResponse"];

export interface HealthResponse {
  status: string;
}
