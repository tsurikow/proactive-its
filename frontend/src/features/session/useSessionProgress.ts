import { useMemo, useState } from "react";

import type { AuthLearner, PlanProgress, PlanTreeNode, StageInfo, TeacherAction, TeacherSessionResult } from "../../types/api";

export interface SessionProgressState {
  plan: PlanProgress | null;
  currentStage: StageInfo | null;
  currentTeacherAction: TeacherAction | null;
  planCompleted: boolean;
  learnerLabel: string;
  stageCountLabel: string;
  progress: number;
  currentTitle: string;
  currentBreadcrumb: string;
  totalStages: number;
  completedStages: number;
  masteryScore: number;
  masteryCaption: string;
  planTree: PlanTreeNode | null;
}

export interface SessionProgressActions {
  applyResult: (result: TeacherSessionResult) => void;
  reset: () => void;
}

export function useSessionProgress(authLearner: AuthLearner | null): SessionProgressState & SessionProgressActions {
  const [plan, setPlan] = useState<PlanProgress | null>(null);
  const [currentStage, setCurrentStage] = useState<StageInfo | null>(null);
  const [currentTeacherAction, setCurrentTeacherAction] = useState<TeacherAction | null>(null);
  const [planCompleted, setPlanCompleted] = useState(false);

  const learnerLabel = useMemo(
    () => (authLearner?.display_name ? authLearner.display_name : authLearner?.email ? authLearner.email : "No learner"),
    [authLearner],
  );

  const stageCountLabel = useMemo(() => {
    if (!plan?.total_stages) {
      return "Stage -/-";
    }
    const stageValue = currentStage
      ? currentStage.stage_index + 1
      : Math.min(plan.completed_stages, plan.total_stages);
    return `Stage ${stageValue}/${plan.total_stages}`;
  }, [currentStage, plan]);

  const progress = useMemo(() => {
    if (!plan?.total_stages) {
      return 0;
    }
    return Math.min(100, Math.round((plan.completed_stages / plan.total_stages) * 100));
  }, [plan]);

  const currentTitle = currentStage?.title || currentStage?.section_id || "Teacher session";
  const currentBreadcrumb = currentStage?.breadcrumb?.join(" → ") || "Start the session to load the first section";
  const totalStages = plan?.total_stages ?? 0;
  const completedStages = plan?.completed_stages ?? 0;
  const masteryScore = plan?.mastery_score ?? 0;

  const applyResult = (result: TeacherSessionResult) => {
    setPlan((result.plan as PlanProgress | null) ?? null);
    setCurrentStage((result.current_stage as StageInfo | null) ?? null);
    setCurrentTeacherAction((result.teacher_action as TeacherAction | null) ?? null);
    setPlanCompleted(Boolean(result.plan_completed));
  };

  const reset = () => {
    setPlan(null);
    setCurrentStage(null);
    setCurrentTeacherAction(null);
    setPlanCompleted(false);
  };

  return {
    plan,
    currentStage,
    currentTeacherAction,
    planCompleted,
    learnerLabel,
    stageCountLabel,
    progress,
    currentTitle,
    currentBreadcrumb,
    totalStages,
    completedStages,
    masteryScore,
    masteryCaption: `${Math.round(masteryScore * 100)}% current mastery signal`,
    planTree: plan?.tree ?? null,
    applyResult,
    reset,
  };
}
