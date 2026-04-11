from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas import LessonPayload
from app.state.models import AdaptationContext

FORBIDDEN_CONTROL_CHARS_MARKER = "forbidden_control_chars"


def _find_forbidden_control_char_path(value: Any, path: str) -> str | None:
    if isinstance(value, str):
        for index, char in enumerate(value):
            codepoint = ord(char)
            if (codepoint < 32 and char not in {"\n", "\r", "\t"}) or codepoint == 127:
                return f"{path}[{index}]"
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            violation = _find_forbidden_control_char_path(item, child_path)
            if violation is not None:
                return violation
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            violation = _find_forbidden_control_char_path(item, f"{path}[{index}]")
            if violation is not None:
                return violation
        return None
    return None


def is_forbidden_control_char_error(exc: Exception) -> bool:
    return FORBIDDEN_CONTROL_CHARS_MARKER in str(exc or "")


class ControlCharValidatedModel(BaseModel):
    @model_validator(mode="after")
    def _validate_control_chars(self) -> "ControlCharValidatedModel":
        violation = _find_forbidden_control_char_path(self.model_dump(mode="python"), self.__class__.__name__)
        if violation is not None:
            raise ValueError(f"{FORBIDDEN_CONTROL_CHARS_MARKER}:{violation}")
        return self


class TeacherSessionEventType(str, Enum):
    OPEN_SESSION = "open_session"
    LEARNER_REPLY = "learner_reply"
    REQUEST_MOVE_ON = "request_move_on"
    ACCEPT_PROPOSAL = "accept_proposal"
    REFUSE_PROPOSAL = "refuse_proposal"
    CONTINUE_SESSION = "continue_session"


class LearnerUnderstandingSignal(str, Enum):
    CONFUSED = "confused"
    PARTIAL = "partial"
    CLEAR = "clear"


class LearnerTurnIntentType(str, Enum):
    NAVIGATION = "navigation"
    TASK_ANSWER = "task_answer"
    CONTENT_QUESTION = "content_question"
    UNDERSTANDING_SIGNAL = "understanding_signal"
    ACKNOWLEDGEMENT = "acknowledgement"


class LearnerNavigationAction(str, Enum):
    CONTINUE_CURRENT_SECTION = "continue_current_section"
    ADVANCE_TO_NEXT_SECTION = "advance_to_next_section"
    REPEAT_CURRENT_SECTION = "repeat_current_section"
    REVISIT_SECTION = "revisit_section"
    ACCEPT_PROPOSAL = "accept_proposal"
    REFUSE_PROPOSAL = "refuse_proposal"


class TeacherActionType(str, Enum):
    TEACH_SECTION = "teach_section"
    ASK_SECTION_QUESTION = "ask_section_question"
    ASSIGN_SECTION_EXERCISE = "assign_section_exercise"
    CHECK_STUDENT_ANSWER = "check_student_answer"
    PROPOSE_CONTINUE = "propose_continue"
    PROPOSE_ADVANCE = "propose_advance"
    PROPOSE_REVISIT = "propose_revisit"
    PROPOSE_SKIP = "propose_skip"
    ACKNOWLEDGE_CHOICE = "acknowledge_choice"
    CLARIFY_STUDENT_QUESTION = "clarify_student_question"
    SUMMARIZE_PROGRESS = "summarize_progress"
    WAIT_FOR_STUDENT_REPLY = "wait_for_student_reply"


class InteractionRouteType(str, Enum):
    GROUNDED_REPLY = "grounded_reply"
    PEDAGOGICAL_REPLY = "pedagogical_reply"
    CLARIFY_BEFORE_RETRIEVAL = "clarify_before_retrieval"


class TeacherProposalType(str, Enum):
    CONTINUE_CURRENT_SECTION = "continue_current_section"
    ADVANCE_TO_NEXT_SECTION = "advance_to_next_section"
    REVISIT_PREVIOUS_SECTION = "revisit_previous_section"
    SKIP_CURRENT_SECTION = "skip_current_section"


class SectionSemanticType(str, Enum):
    INTRODUCTION = "introduction"
    CORE_CONCEPT = "core_concept"
    FORMULA_REFERENCE = "formula_reference"
    WORKED_EXAMPLE = "worked_example"
    CHECKPOINT = "checkpoint"
    EXERCISE_BANK = "exercise_bank"
    REVIEW = "review"
    TRANSITION = "transition"


class LearningDebtKind(str, Enum):
    SKIPPED_SECTION = "skipped_section"
    REFUSED_REVISIT = "refused_revisit"
    UNANSWERED_CHECKPOINT = "unanswered_checkpoint"
    UNATTEMPTED_EXERCISE = "unattempted_exercise"
    UNRESOLVED_EXERCISE = "unresolved_exercise"
    MOVED_ON_WEAK = "moved_on_weak"


class LearningDebtStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CheckpointEvaluationStatus(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    UNRESOLVED = "unresolved"


class RepairMode(str, Enum):
    EXPLAIN_BRIEF = "explain_brief"
    HINT_BRIEF = "hint_brief"
    REASK_TIGHTER = "reask_tighter"


class PendingTeacherTaskKind(str, Enum):
    CHECKPOINT_QUESTION = "checkpoint_question"
    SECTION_EXERCISE = "section_exercise"


class TeacherDecisionKind(str, Enum):
    TEACHER_ACTION = "teacher_action"
    INTERACTION_ROUTE = "interaction_route"
    LEARNER_TURN_INTENT = "learner_turn_intent"
    WEAK_ANSWER = "weak_answer"
    PROGRESSION = "progression"


class TeacherMessageKind(str, Enum):
    PEDAGOGICAL_REPLY = "pedagogical_reply"
    PROGRESSION_FRAMING = "progression_framing"
    LEARNER_MEMORY = "learner_memory"
    LESSON_PLAN = "lesson_plan"
    GROUNDING_ANALYSIS = "grounding_analysis"


class StageAction(str, Enum):
    CONTINUE = "continue"
    REMEDIATE_SAME_STAGE = "remediate_same_stage"
    REVISIT_PREREQUISITE = "revisit_prerequisite"
    ADVANCE = "advance"


class TeacherSessionContext(BaseModel):
    current_module_id: str | None = None
    current_section_id: str | None = None
    interaction_id: int | None = None


class LearnerSignalPayload(BaseModel):
    understanding_signal: LearnerUnderstandingSignal
    interaction_id: int | None = None


class TeacherProposal(BaseModel):
    proposal_type: TeacherProposalType
    rationale: str = Field(min_length=1, max_length=1200)
    target_section_id: str | None = None
    target_module_id: str | None = None
    target_title: str | None = Field(default=None, max_length=200)
    can_defer: bool = True


class TeacherAction(BaseModel):
    action_type: TeacherActionType
    rationale: str = Field(min_length=1, max_length=1200)
    section_id: str | None = None
    module_id: str | None = None
    target_section_id: str | None = None
    prompt_instruction: str | None = None
    question_prompt: str | None = Field(default=None, max_length=400)
    exercise_ref: str | None = Field(default=None, max_length=200)
    hidden_answer_ref: str | None = Field(default=None, max_length=200)
    requires_learner_reply: bool = True
    allows_move_on: bool = True


class ExerciseCandidate(ControlCharValidatedModel):
    exercise_ref: str = Field(min_length=1, max_length=200)
    prompt_excerpt: str = Field(min_length=1, max_length=600)
    hidden_answer_ref: str | None = Field(default=None, max_length=200)
    requires_answer_check: bool = True


class CheckpointCandidate(ControlCharValidatedModel):
    checkpoint_ref: str = Field(min_length=1, max_length=200)
    prompt_excerpt: str = Field(min_length=1, max_length=600)
    hidden_answer_ref: str | None = Field(default=None, max_length=200)
    requires_answer_check: bool = True


class AnswerCheckContext(ControlCharValidatedModel):
    item_ref: str = Field(min_length=1, max_length=200)
    item_type: Literal["exercise", "checkpoint", "question"]
    hidden_answer_ref: str | None = Field(default=None, max_length=200)
    answer_source_excerpt: str | None = Field(default=None, max_length=700)
    rubric_brief: str | None = Field(default=None, max_length=300)
    can_verify: bool = True


class SectionUnderstanding(ControlCharValidatedModel):
    pedagogical_role: SectionSemanticType
    teaching_intent: str = Field(min_length=1, max_length=1200)
    should_dwell: bool = True
    supports_generated_question: bool = False
    explicit_exercises: list[ExerciseCandidate] = Field(default_factory=list, max_length=6)
    explicit_checkpoints: list[CheckpointCandidate] = Field(default_factory=list, max_length=6)
    answer_check_contexts: list[AnswerCheckContext] = Field(default_factory=list, max_length=8)
    recommended_actions: list[TeacherActionType] = Field(default_factory=list, max_length=12)
    rationale: str = Field(min_length=1, max_length=2000)


class SectionUnderstandingArtifact(SectionUnderstanding):
    section_id: str
    source_hash: str
    parent_doc_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    breadcrumb: list[str] = Field(default_factory=list, max_length=12)
    context_version: str


class TeacherDecisionResult(BaseModel):
    specialist_kind: TeacherDecisionKind
    rationale: str = Field(default="", max_length=1200)
    action_type: TeacherActionType | None = None
    proposal: TeacherProposal | None = None
    question_prompt: str | None = Field(default=None, max_length=800)
    exercise_ref: str | None = Field(default=None, max_length=200)
    hidden_answer_ref: str | None = Field(default=None, max_length=200)
    requires_learner_reply: bool = True
    allows_move_on: bool = True
    route_type: InteractionRouteType | None = None
    clarification_aim: str | None = Field(default=None, max_length=240)
    retrieval_aim: str | None = Field(default=None, max_length=240)
    stage_action: StageAction | None = None
    target_section_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    context_version: str | None = Field(default=None, max_length=120)
    section_id: str | None = None
    item_ref: str | None = Field(default=None, max_length=200)
    teacher_message_fragment: str | None = Field(default=None, max_length=700)
    keeps_pending_task_active: bool = True
    repair_mode: RepairMode | None = None
    clarification_prompt: str | None = Field(default=None, max_length=500)


class TeacherMessageResult(BaseModel):
    message_kind: TeacherMessageKind
    rationale: str | None = Field(default=None, max_length=300)
    teacher_message: str | None = Field(default=None, max_length=1200)
    message_fragment: str | None = Field(default=None, max_length=700)
    learner_state_summary: str | None = Field(default=None, max_length=300)
    misconception_patterns: list[str] = Field(default_factory=list, max_length=4)
    support_priorities: list[str] = Field(default_factory=list, max_length=4)
    strength_signals: list[str] = Field(default_factory=list, max_length=4)
    caution_flags: list[str] = Field(default_factory=list, max_length=4)
    teaching_brief: str | None = Field(default=None, max_length=300)
    lesson_objective: str | None = Field(default=None, max_length=300)
    explanation_arc: list[str] = Field(default_factory=list, max_length=6)
    example_plan: list[str] = Field(default_factory=list, max_length=4)
    checkpoint_plan: list[str] = Field(default_factory=list, max_length=4)
    support_emphasis: str | None = Field(default=None, max_length=200)
    progression_note: str | None = Field(default=None, max_length=240)
    answer_objective: str | None = Field(default=None, max_length=240)
    evidence_priorities: list[str] = Field(default_factory=list, max_length=4)
    explanation_route: list[str] = Field(default_factory=list, max_length=5)
    misconception_or_confusion_risks: list[str] = Field(default_factory=list, max_length=4)
    refusal_posture: str | None = Field(default=None, max_length=200)
    citation_priorities: list[str] = Field(default_factory=list, max_length=4)


class TeacherChatPlan(BaseModel):
    teacher_action: TeacherAction
    proposal: TeacherProposal | None = None
    surface_instruction: str | None = Field(default=None, max_length=1600)
    policy_brief: str | None = Field(default=None, max_length=1600)
    grounding_analysis: TeacherMessageResult | None = None
    allows_move_on: bool = True
    requires_learner_reply: bool = True


class LearningDebtItem(BaseModel):
    debt_kind: LearningDebtKind
    section_id: str
    module_id: str | None = None
    status: LearningDebtStatus = LearningDebtStatus.OPEN
    rationale: str = Field(min_length=1, max_length=300)
    source_interaction_id: int | None = None
    source_action_type: TeacherActionType | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class CheckpointEvaluation(BaseModel):
    status: CheckpointEvaluationStatus
    section_id: str
    exercise_ref: str | None = None
    evaluator_source: Literal["teacher_graph", "fallback"] = "teacher_graph"
    hidden_answer_used: bool = True
    learner_claim_brief: str | None = Field(default=None, max_length=300, exclude=True)
    source_alignment: str | None = Field(default=None, max_length=300, exclude=True)
    missing_or_wrong_piece: str | None = Field(default=None, max_length=300, exclude=True)
    rationale: str = Field(min_length=1, max_length=300)
    teacher_feedback_brief: str = Field(min_length=1, max_length=300)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PendingTeacherTask(BaseModel):
    task_kind: PendingTeacherTaskKind
    section_id: str
    prompt_excerpt: str = Field(min_length=1, max_length=1200)
    item_ref: str = Field(min_length=1, max_length=200)
    hidden_answer_ref: str | None = Field(default=None, max_length=200)
    answer_check_context: AnswerCheckContext | None = None
    resolved: bool = False
    attempt_count: int = Field(default=0, ge=0)


class WeakAnswerDecisionKind(str, Enum):
    REPAIR = "repair"
    CLARIFY = "clarify"
    REVISIT = "revisit"

class RepairHistorySummary(BaseModel):
    weak_attempt_ordinal: int = Field(default=1, ge=1)
    recent_repair_modes: list[RepairMode] = Field(default_factory=list, max_length=3)
    recent_weak_statuses: list[Literal["partial", "incorrect", "unresolved"]] = Field(
        default_factory=list,
        max_length=3,
    )
    last_repair_mode: RepairMode | None = None
    last_weak_status: Literal["partial", "incorrect", "unresolved"] | None = None
    previous_weak_status: Literal["partial", "incorrect", "unresolved"] | None = None
    stayed_vague_after_reask: bool = False
    stayed_wrong_after_hint: bool = False
    repeated_same_mode_risk: bool = False
    recent_clarify_count: int = Field(default=0, ge=0)
    last_clarification_used: bool = False
    trajectory_summary: str | None = Field(default=None, max_length=240)

class TeacherArtifactReference(BaseModel):
    id: int | None = None
    decision_kind: str = Field(min_length=1, max_length=120)
    artifact_key: str = Field(min_length=1, max_length=255)
    stage_index: int
    section_id: str = Field(min_length=0, max_length=200)
    module_id: str | None = Field(default=None, max_length=200)
    context_version: str | None = Field(default=None, max_length=120)
    stage_signal: str | None = Field(default=None, max_length=120)
    created_at: datetime | None = None


class TeacherTurnContext(BaseModel):
    """Legacy context model — kept for backward-compatible deserialization in ChatService."""
    model_config = ConfigDict(extra="ignore")

    learner_id: str = ""
    current_stage: dict[str, Any] | None = None
    template_id: str = ""
    working_turn_context: Any | None = None


class TeacherArtifactContext(BaseModel):
    current_stage: dict[str, Any] | None = None
    adaptation_context: AdaptationContext | None = None
    teacher_chat_plan: TeacherChatPlan | None = None
    learner_memory_summary: TeacherMessageResult | None = None
    grounding_summary: dict[str, Any] = Field(default_factory=dict)
    recent_evidence_summary: dict[str, Any] = Field(default_factory=dict)
    recent_decision_summary: dict[str, Any] = Field(default_factory=dict)
    learner_question: str | None = None
    learner_response: str | None = None
    tutor_answer: str | None = None


class StageSourcePayload(ControlCharValidatedModel):
    section_id: str
    parent_doc_id: str
    title: str | None = None
    breadcrumb: list[str] = Field(default_factory=list)
    source_markdown: str
    source_hash: str


class TeacherArtifact(BaseModel):
    id: int | None = None
    learner_id: str
    template_id: str
    stage_index: int
    section_id: str
    module_id: str | None = None
    decision_kind: str
    artifact_key: str
    stage_signal: str
    decision_source: str
    context_version: str
    effective_mastery_score: float | None = Field(default=None, ge=0.0, le=1.0)
    weak_topic_count: int = Field(ge=0)
    module_evidence_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    fallback_reason: str | None = None
    decision_payload_json: dict[str, Any] | None = None
    created_at: datetime | None = None


class TeacherSessionRequest(BaseModel):
    learner_id: str | None = None
    event_type: TeacherSessionEventType
    message: str | None = None
    context: TeacherSessionContext = Field(default_factory=TeacherSessionContext)
    learner_signal: LearnerSignalPayload | None = None
    proposal_type: TeacherProposalType | None = None
    force_move: bool = False


class TeacherSessionResult(BaseModel):
    teacher_message: str = Field(min_length=1)
    teacher_action: TeacherAction
    proposal: TeacherProposal | None = None
    checkpoint_evaluation: CheckpointEvaluation | None = None
    debt_updates: list[LearningDebtItem] = Field(default_factory=list)
    section_understanding: SectionUnderstandingArtifact | None = None
    current_stage: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    plan_completed: bool = False
    lesson: LessonPayload | None = None
    interaction_id: int | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_debug: dict[str, Any] | None = None


