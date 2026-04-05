import type { LessonPayload } from "../../types/api";

export interface FeedMessage {
  id: string;
  role: "assistant" | "user" | "system" | "error";
  kind?: "lesson" | "chat" | "system" | "error";
  title?: string;
  content: string;
  citations?: import("../../types/api").Citation[];
  interactionId?: number;
  checkpointEvaluation?: import("../../types/api").CheckpointEvaluation | null;
}

function messageId(): string {
  if ("randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function sanitizeLessonContent(content: string): string {
  const lines = content.split("\n");
  if (lines.length === 0) {
    return content;
  }
  const first = lines[0].trim();
  if (/^(Goal|Concept|Summary|Definition|Example|Check|Remediation)\s*:/i.test(first)) {
    return lines.slice(1).join("\n").trim();
  }
  return content;
}

export function toLessonMessages(lesson: LessonPayload | null): FeedMessage[] {
  if (!lesson) {
    return [];
  }
  const steps = [...(lesson.lesson_steps ?? [])].sort((a, b) => a.order_index - b.order_index);
  return steps.map((step) => ({
    id: messageId(),
    role: "assistant",
    kind: "lesson",
    content: sanitizeLessonContent(step.content_md),
  }));
}

export function nextMessageId(): string {
  return messageId();
}
