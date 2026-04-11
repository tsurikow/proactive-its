import type { components } from "./generated/api";

export type LessonStep = components["schemas"]["LessonStep"];
export type StageInfo = components["schemas"]["StageInfo"];
export interface PlanTreeNode {
  id: string;
  title: string;
  status?: string | null;
  children?: PlanTreeNode[];
}

export interface PlanProgress {
  total_stages: number;
  completed_stages: number;
  mastery_score: number;
  tree?: PlanTreeNode | null;
}
export type LessonPayload = components["schemas"]["LessonPayload"];
export type TeacherAction = components["schemas"]["TeacherAction"];
export type CheckpointEvaluation = components["schemas"]["CheckpointEvaluation"];

export type TeacherSessionRequest = Omit<components["schemas"]["TeacherSessionRequest"], "learner_id"> & {
  learner_id?: string | null;
};

export interface AuthLearner {
  id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  email: string;
  is_active: boolean;
}

export interface AuthResponse {
  learner: AuthLearner;
}

export interface Citation {
  chunk_id: string;
  doc_id: string;
  title: string;
  breadcrumb: string[];
  quote: string;
}

export interface RetrievalDebug {
  top_k?: number;
  filtered_by?: Record<string, string>;
  scores?: Array<Record<string, number | string>>;
  top_score?: number | null;
  citation_fallback_used?: boolean | null;
  rewrite_attempted?: boolean | null;
  rewrite_query?: string | null;
  rewrite_accepted?: boolean | null;
  rewrite_reason?: string | null;
  evidence_chars?: number | null;
  weak_evidence?: boolean | null;
  eligible_query_term_count?: number | null;
  matched_query_term_count?: number | null;
  matched_query_terms?: string[] | null;
  query_overlap_ratio?: number | null;
  offtopic_suspected?: boolean | null;
  weak_evidence_reason?: string | null;
  retrieval_mode?: string | null;
  timings_ms?: Record<string, number> | null;
}

export type TeacherSessionResult = Omit<
  components["schemas"]["TeacherSessionResult"],
  "current_stage" | "plan" | "lesson" | "citations" | "retrieval_debug"
> & {
  current_stage?: StageInfo | null;
  plan?: PlanProgress | null;
  lesson?: LessonPayload | null;
  citations?: Citation[];
  retrieval_debug?: RetrievalDebug | null;
};

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  database_ready: boolean;
  template_ready: boolean;
  template_id: string | null;
  template_version: number | null;
  content_ready: boolean;
  sections_count: number;
  chunks_count: number;
}
