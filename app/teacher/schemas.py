"""
SGR (Schema-Guided Reasoning) output schemas for teacher agent.

Each schema is a reasoning chain where fields guide the LLM through analysis
before reaching a decision. Field ORDER matters — reasoning fields come first,
decision fields last.

These schemas replace the god objects (TeacherDecisionResult, TeacherMessageResult)
and the transport model layer. Each schema is specific to ONE cognitive task.

Reference: https://abdullin.com/schema-guided-reasoning/
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.teacher.models import (
    CheckpointEvaluationStatus,
    InteractionRouteType,
    LearnerNavigationAction,
    LearnerTurnIntentType,
    RepairMode,
    SectionSemanticType,
    TeacherActionType,
    TeacherProposalType,
    WeakAnswerDecisionKind,
)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class SGRSchema(BaseModel):
    """Base for all SGR output schemas. Strict mode, no extra fields."""
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 1. Intent & Route — classify learner message + decide response strategy
#
# Used when learner sends a message. Combines what were previously two
# separate agent calls (learner_turn_intent + interaction_route).
#
# Reasoning chain:
#   message_read → context_read → intent_evidence
#   → intent_type → route_reasoning → route_type
# ---------------------------------------------------------------------------

class IntentAndRoute(SGRSchema):
    """Classify learner intent and decide how to respond in one pass."""

    # --- Reasoning chain (LLM fills these BEFORE making decisions) ---

    message_read: str = Field(
        description=(
            "Restate what the learner is saying or asking in your own words. "
            "Note tone, specificity, and whether it looks like an answer attempt, "
            "a question, a navigation request, or a social/acknowledgement signal."
        ),
    )
    context_read: str = Field(
        description=(
            "Summarize the current teaching situation: is there a pending task "
            "the learner should be answering? An active proposal they might be "
            "accepting or refusing? What section are we in?"
        ),
    )
    intent_evidence: str = Field(
        description=(
            "What specific evidence in the message points to the intent type "
            "you will choose? Cite key phrases or structural clues."
        ),
    )

    # --- Decision: intent ---

    intent_type: LearnerTurnIntentType = Field(
        description=(
            "The learner's primary intent. "
            "task_answer: answering the current pending task. "
            "content_question: asking a subject-matter question. "
            "navigation: requesting to move (advance, revisit, repeat, accept/refuse proposal). "
            "understanding_signal: expressing confusion or partial understanding. "
            "acknowledgement: short confirmation, readiness signal, social reply."
        ),
    )
    navigation_action: LearnerNavigationAction | None = Field(
        default=None,
        description="Required when intent_type=navigation. Which navigation action?",
    )
    proposal_type: TeacherProposalType | None = Field(
        default=None,
        description="Required when navigation_action is accept_proposal or refuse_proposal.",
    )
    target_section_id: str | None = Field(
        default=None,
        description="Target section id when navigation_action=revisit_section and section is clearly identified.",
    )
    target_title: str | None = Field(
        default=None,
        description="Learner's description of the target when revisiting but section_id is unclear.",
    )

    # --- Decision: route ---

    route_reasoning: str = Field(
        description=(
            "Why this response route fits the classified intent. "
            "grounded_reply: the question needs source retrieval to answer well. "
            "pedagogical_reply: can be handled directly as a teacher turn. "
            "clarify_before_retrieval: message is too vague to retrieve for safely."
        ),
    )
    route_type: InteractionRouteType = Field(
        description="How the teacher should respond to this message.",
    )
    retrieval_aim: str | None = Field(
        default=None,
        description="What to retrieve from the textbook, if route_type=grounded_reply.",
    )
    clarification_aim: str | None = Field(
        default=None,
        description="What single detail to ask for, if route_type=clarify_before_retrieval.",
    )


# ---------------------------------------------------------------------------
# 2. Teacher Turn — decide action + generate message
#
# The main "what does the teacher do and say" schema. Used for:
# - Session open / continue (presenting material, assigning tasks)
# - Pedagogical replies (non-RAG responses)
# - Post-evaluation follow-ups
# - Navigation acknowledgements
#
# Reasoning chain:
#   situation_read → learner_state_read → pedagogical_reasoning
#   → action_type → teacher_message (+ optional proposal)
# ---------------------------------------------------------------------------

class TeacherProposalOutput(SGRSchema):
    """A navigation proposal the teacher makes to the learner."""

    proposal_type: TeacherProposalType = Field(
        description="What kind of movement is being proposed.",
    )
    target_section_id: str | None = Field(
        default=None,
        description="Target section for revisit or advance proposals.",
    )
    target_title: str | None = Field(
        default=None,
        description="Learner-facing title of the target section.",
    )
    can_defer: bool = Field(
        default=True,
        description="Whether the learner may decline and continue the current path.",
    )
    rationale: str = Field(
        description="Why this proposal is pedagogically appropriate right now.",
    )


class TeacherTurn(SGRSchema):
    """Decide the teacher's next action and generate the message."""

    # --- Reasoning chain ---

    situation_read: str = Field(
        description=(
            "What just happened? Summarize the trigger: session open, learner reply, "
            "accepted proposal, post-evaluation, etc. What section are we in?"
        ),
    )
    learner_state_read: str = Field(
        description=(
            "What do we know about this learner? Strengths, struggles, pace, "
            "recent performance, outstanding learning debt."
        ),
    )
    section_context_read: str = Field(
        description=(
            "What does the current section contain that's relevant? "
            "Key concepts, available exercises/checkpoints, teaching intent. "
            "Are there mandatory tasks the learner hasn't attempted yet?"
        ),
    )
    pedagogical_reasoning: str = Field(
        description=(
            "Why is the chosen action the best pedagogical move right now? "
            "Consider: has the learner understood the core idea? Should we "
            "dwell longer, move on, assign a task, or revisit earlier material?"
        ),
    )

    # --- Decision: action ---

    action_type: TeacherActionType = Field(
        description=(
            "The teacher's next action. "
            "teach_section: present or continue presenting material. "
            "ask_section_question: ask a comprehension question. "
            "assign_section_exercise: assign a textbook exercise/checkpoint. "
            "propose_advance: suggest moving to the next section. "
            "propose_revisit: suggest revisiting an earlier section. "
            "propose_continue: suggest continuing the current section. "
            "acknowledge_choice: acknowledge a learner navigation choice. "
            "summarize_progress: recap what we've covered. "
            "wait_for_student_reply: wait for learner to respond."
        ),
    )
    exercise_ref: str | None = Field(
        default=None,
        description="Reference to the exercise/checkpoint being assigned (from section understanding).",
    )
    question_prompt: str | None = Field(
        default=None,
        description="The question text if asking a comprehension question.",
    )

    # --- Optional proposal ---

    proposal: TeacherProposalOutput | None = Field(
        default=None,
        description="Navigation proposal, required when action_type is propose_advance/revisit/continue/skip.",
    )

    # --- Output: teacher message ---

    teacher_message: str = Field(
        description=(
            "The teacher's message to the learner. Natural, conversational, "
            "pedagogically appropriate. Preserves KaTeX formulas and figure links "
            "from the source material. Never reveals exercise/checkpoint solutions "
            "unless the learner explicitly asks."
        ),
    )


# ---------------------------------------------------------------------------
# 3. Answer Evaluation — check learner's answer to a task
#
# SGR chain from CheckpointEvaluationTransport (already well-designed):
#   task_read → verification_read → learner_claim_brief → source_alignment
#   → missing_or_wrong_piece → verdict_basis → status
#
# This schema produces correct evaluations that need NO post-processing.
# ---------------------------------------------------------------------------

class AnswerEvaluation(SGRSchema):
    """Evaluate a learner's answer to a checkpoint or exercise."""

    # --- Reasoning chain ---

    task_read: str = Field(
        description="Restate what the learner was required to supply or conclude.",
    )
    verification_read: str = Field(
        description="Summarize the source-backed verification basis that controls the grading decision.",
    )
    learner_claim_brief: str = Field(
        description="What is the learner claiming or attempting in their response?",
    )
    source_alignment: str = Field(
        description="How does the learner response align or fail to align with the source-backed criteria?",
    )
    missing_or_wrong_piece: str | None = Field(
        default=None,
        description=(
            "The most important missing, incorrect, or unresolved piece "
            "preventing a fully correct answer. Null only if fully correct."
        ),
    )
    verdict_basis: str = Field(
        description="Why the chosen verdict follows from the verification basis.",
    )

    # --- Decision ---

    status: CheckpointEvaluationStatus = Field(
        description=(
            "Final verdict. "
            "correct: answer meets all source-backed criteria. "
            "partial: core idea present but missing a required piece. "
            "incorrect: wrong concept, rule, or conclusion. "
            "unresolved: too vague or underspecified to verify. "
            "skipped: learner explicitly declined to answer."
        ),
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in this verdict, 0.0 to 1.0.",
    )

    # --- Output for learner ---

    teacher_feedback_brief: str = Field(
        description=(
            "Short learner-facing feedback. If correct, acknowledge what they got right. "
            "If not correct, describe what's missing WITHOUT revealing the answer. "
            "Guide the learner toward the right thinking."
        ),
    )


# ---------------------------------------------------------------------------
# 4. Weak Answer Plan — decide how to respond to incorrect/partial answers
#
# Used after AnswerEvaluation returns non-correct status.
# Decides: give a hint, explain briefly, reask more tightly, or give up and revisit.
#
# Reasoning chain:
#   evaluation_read → attempt_history_read → convergence_assessment
#   → decision_kind → repair details
# ---------------------------------------------------------------------------

class WeakAnswerPlan(SGRSchema):
    """Plan the teacher's response to an incorrect, partial, or unresolved answer."""

    # --- Reasoning chain ---

    evaluation_read: str = Field(
        description="What did the evaluation say? What is the learner getting wrong or missing?",
    )
    attempt_history_read: str = Field(
        description=(
            "How many attempts has the learner made? What repair strategies "
            "have already been tried? Is there a pattern (vague loop, wrong answer loop, etc.)?"
        ),
    )
    convergence_assessment: str = Field(
        description=(
            "Is the learner converging toward a correct answer, stuck in a loop, "
            "or getting further away? What's the best move to break the pattern?"
        ),
    )

    # --- Decision ---

    decision_kind: WeakAnswerDecisionKind = Field(
        description=(
            "repair: give a hint or brief explanation to help the learner fix their answer. "
            "clarify: ask for one specific missing detail (only when answer is unresolved). "
            "revisit: abandon this task and suggest revisiting prerequisite material "
            "(only when multiple attempts show no convergence)."
        ),
    )
    repair_mode: RepairMode | None = Field(
        default=None,
        description=(
            "Required when decision_kind=repair. "
            "hint_brief: give a targeted hint without revealing the answer. "
            "explain_brief: briefly explain the concept the learner is missing. "
            "reask_tighter: restate the question more narrowly to reduce ambiguity."
        ),
    )

    # --- Output ---

    teacher_message: str = Field(
        description=(
            "The teacher's message to the learner. If repair, include the hint or "
            "explanation. If clarify, ask for the missing detail. If revisit, "
            "explain why we're stepping back and propose revisiting."
        ),
    )
    proposal: TeacherProposalOutput | None = Field(
        default=None,
        description="Revisit proposal, required only when decision_kind=revisit.",
    )


# ---------------------------------------------------------------------------
# 5. Section Understanding — analyze a textbook section for teaching
#
# Combines what were previously two separate agents (semantics + tasks).
# Cached by section_id + source_hash. Run once per section.
#
# Reasoning chain:
#   section_read → task_signal_read → role_basis
#   → pedagogical_role → teaching_intent → exercises/checkpoints
# ---------------------------------------------------------------------------

class ExerciseCandidateOutput(SGRSchema):
    """A source-backed exercise extracted from the section."""

    exercise_ref: str = Field(description="Stable source-local reference (e.g., 'exercise_2.1.3').")
    prompt_excerpt: str = Field(description="The exercise prompt, compact and source-faithful.")
    hidden_answer_ref: str | None = Field(
        default=None, description="Source reference to the answer material, if available.",
    )
    requires_answer_check: bool = Field(
        description="Whether this exercise has enough basis for automated answer checking.",
    )


class CheckpointCandidateOutput(SGRSchema):
    """A source-backed checkpoint extracted from the section."""

    checkpoint_ref: str = Field(description="Stable source-local reference (e.g., 'checkpoint_2.1').")
    prompt_excerpt: str = Field(description="The checkpoint prompt, compact and source-faithful.")
    hidden_answer_ref: str | None = Field(
        default=None, description="Source reference to the answer material, if available.",
    )
    requires_answer_check: bool = Field(
        description="Whether this checkpoint has enough basis for automated answer checking.",
    )
    source_kind: Literal["literal", "derived_review"] = Field(
        default="literal",
        description="Whether this is directly from the source or a derived review question.",
    )


class AnswerCheckContextOutput(SGRSchema):
    """Verification context for checking a learner's answer to an exercise or checkpoint."""

    item_ref: str = Field(description="Which exercise/checkpoint this context belongs to.")
    item_type: Literal["exercise", "checkpoint", "question"] = Field(
        description="Type of the item being checked.",
    )
    hidden_answer_ref: str | None = Field(
        default=None, description="Source reference to hidden answer material.",
    )
    answer_source_excerpt: str | None = Field(
        default=None, description="Compact source excerpt supporting verification.",
    )
    rubric_brief: str | None = Field(
        default=None, description="Essential correctness criteria for evaluation.",
    )
    can_verify: bool = Field(
        description="Whether this context provides enough basis for reliable verification.",
    )


class SectionUnderstanding(SGRSchema):
    """Analyze a textbook section to understand what it contains and how to teach it."""

    # --- Reasoning chain ---

    section_read: str = Field(
        description="What is the dominant surface of this section? Theory, examples, exercises, review?",
    )
    task_signal_read: str = Field(
        description="Does the section contain real learner-facing tasks (exercises, checkpoints)?",
    )
    role_basis: str = Field(
        description="Why does the chosen pedagogical role fit the source material?",
    )

    # --- Decision: section semantics ---

    pedagogical_role: SectionSemanticType = Field(
        description=(
            "The section's pedagogical role. "
            "introduction, core_concept, formula_reference, worked_example, "
            "checkpoint, exercise_bank, review, transition."
        ),
    )
    teaching_intent: str = Field(
        description="Teacher-facing summary: what is this section mainly trying to teach?",
    )
    should_dwell: bool = Field(
        description="Should the teacher spend meaningful time here, or move through quickly?",
    )
    supports_generated_question: bool = Field(
        description="Is a teacher-generated comprehension question appropriate for this section?",
    )
    recommended_actions: list[TeacherActionType] = Field(
        description="Which teacher actions best fit this section (teach, ask, assign, etc.).",
    )

    # --- Decision: extracted tasks ---

    explicit_exercises: list[ExerciseCandidateOutput] = Field(
        default_factory=list,
        description="Exercises extracted from the source. Only real, source-backed exercises.",
    )
    explicit_checkpoints: list[CheckpointCandidateOutput] = Field(
        default_factory=list,
        description="Checkpoints extracted from the source.",
    )
    answer_check_contexts: list[AnswerCheckContextOutput] = Field(
        default_factory=list,
        description="Verification contexts for the extracted exercises and checkpoints.",
    )

    # --- Final rationale ---

    rationale: str = Field(
        description="Compact explanation of the overall section understanding decision.",
    )


# ---------------------------------------------------------------------------
# 6. Learner Memory Synthesis — build/update persistent learner model
#
# Run after significant interactions. Produces a structured summary
# that persists across sessions and is passed as context to every call.
#
# Reasoning chain:
#   session_evidence_read → pattern_analysis
#   → strengths/misconceptions/pace → teaching_recommendations
# ---------------------------------------------------------------------------

class LearnerMemory(SGRSchema):
    """Synthesize or update the persistent learner model from session evidence."""

    # --- Reasoning chain ---

    session_evidence_read: str = Field(
        description=(
            "What happened in this session? Key interactions, answers given, "
            "topics covered, questions asked, difficulties encountered."
        ),
    )
    pattern_analysis: str = Field(
        description=(
            "What patterns emerge? Is the learner quick or slow? "
            "Do they struggle with specific types of problems? "
            "Are they engaged or disengaged? Any recurring misconceptions?"
        ),
    )

    # --- Updated learner model ---

    strengths: list[str] = Field(
        default_factory=list,
        description="Topics and skills where the learner shows solid understanding.",
    )
    misconceptions: list[str] = Field(
        default_factory=list,
        description="Specific misconceptions or recurring errors observed.",
    )
    pace_observation: str = Field(
        description="How fast does the learner move through material? Do they need more or less time?",
    )
    engagement_level: Literal["high", "moderate", "low", "unclear"] = Field(
        description="Overall engagement based on response quality, questions asked, effort shown.",
    )
    learning_debt_summary: str = Field(
        description="Outstanding topics or tasks the learner skipped or didn't master.",
    )

    # --- Recommendations ---

    teaching_recommendations: str = Field(
        description=(
            "How should the teacher adapt for this learner? "
            "More examples? Slower pace? Skip basics? Focus on specific areas?"
        ),
    )
    priority_revisit_topics: list[str] = Field(
        default_factory=list,
        description="Topics that should be revisited in the next session, ordered by priority.",
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "SGRSchema",
    "IntentAndRoute",
    "TeacherTurn",
    "TeacherProposalOutput",
    "AnswerEvaluation",
    "WeakAnswerPlan",
    "SectionUnderstanding",
    "ExerciseCandidateOutput",
    "CheckpointCandidateOutput",
    "AnswerCheckContextOutput",
    "LearnerMemory",
]
