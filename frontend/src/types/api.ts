export type LessonStepType =
  | "goal"
  | "definition"
  | "concept"
  | "example"
  | "check"
  | "remediation"
  | "summary";

export interface LessonStep {
  step_id: string;
  step_type: LessonStepType;
  title: string;
  content_md: string;
  source_chunk_ids: string[];
  order_index: number;
}

export interface StageInfo {
  stage_index: number;
  section_id: string;
  module_id: string | null;
  parent_doc_id?: string | null;
  title: string | null;
  breadcrumb: string[];
}

export interface PlanProgress {
  template_id: string;
  total_stages: number;
  completed_stages: number;
}

export interface LessonPayload {
  section_summary_md: string | null;
  lesson_steps: LessonStep[];
  cached: boolean;
  generation_mode?: string;
  preservation_report?: {
    passed: boolean;
    checks?: Record<string, boolean>;
    prose_overlap?: number;
    min_required_overlap?: number;
    [key: string]: unknown;
  } | null;
}

export interface StartResponse {
  message: string;
  plan: PlanProgress;
  current_stage: StageInfo | null;
  plan_completed: boolean;
}

export interface LessonCurrentResponse {
  current_stage: StageInfo | null;
  lesson: LessonPayload | null;
  plan_completed: boolean;
}

export interface NextResponse {
  message: string;
  current_stage: StageInfo | null;
  plan_completed: boolean;
}

export interface Citation {
  chunk_id: string;
  doc_id: string;
  title: string;
  breadcrumb: string[];
  quote: string;
}

export interface RetrievalDebug {
  top_k: number;
  filtered_by: Record<string, string>;
  scores: Array<{ chunk_id: string; score: number }>;
  top_score?: number;
  evidence_chars?: number;
  weak_evidence?: boolean;
  retrieval_mode?: string;
}

export interface ChatResponse {
  interaction_id: number;
  answer_md: string;
  citations: Citation[];
  retrieval_debug?: RetrievalDebug | null;
}

export interface HealthResponse {
  status: string;
}
