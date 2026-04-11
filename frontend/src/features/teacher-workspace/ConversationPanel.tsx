import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";

import type { ComposerState, SessionStatusState, TranscriptState } from "../session/types";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";
import { TypingIndicator } from "./TypingIndicator";

interface ConversationPanelProps {
  status: SessionStatusState;
  transcript: TranscriptState;
  composer: ComposerState;
}

export function ConversationPanel({ status, transcript, composer }: ConversationPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const isNearBottomRef = useRef(true);
  const messageRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !isNearBottomRef.current || transcript.focusMessageId) {
      return;
    }
    root.scrollTo({ top: root.scrollHeight, behavior: "smooth" });
  }, [transcript.focusMessageId, transcript.messages]);

  useEffect(() => {
    if (!transcript.focusMessageId) {
      return;
    }
    const root = scrollRef.current;
    const target = messageRefs.current[transcript.focusMessageId];
    if (!root || !target) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const rootRect = root.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const offsetTop = targetRect.top - rootRect.top + root.scrollTop - 16;
      root.scrollTo({ top: Math.max(0, offsetTop), behavior: "smooth" });
      transcript.clearFocusMessageId();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [transcript.focusMessageId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        onScroll={(event) => {
          const root = event.currentTarget;
          isNearBottomRef.current = root.scrollHeight - root.scrollTop - root.clientHeight <= 100;
        }}
        className="flex-1 overflow-y-auto overscroll-y-contain px-3 py-4 sm:px-4"
      >
        <div className="mx-auto flex min-h-full max-w-3xl flex-col">
          {transcript.messages.length === 0 ? (
            <div className="flex flex-1 items-center justify-center">
              <EmptyState />
            </div>
          ) : (
            <div className="space-y-3">
              {transcript.messages.map((message) => (
                <div
                  key={message.id}
                  ref={(node) => {
                    messageRefs.current[message.id] = node;
                  }}
                  className="msg-enter"
                >
                  <MessageBubble message={message} />
                </div>
              ))}
              {status.pendingStatus ? <TypingIndicator text={status.pendingStatus.text} /> : null}
            </div>
          )}
        </div>
      </div>

      <Composer composer={composer} />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[color:var(--accent-soft)] text-[color:var(--accent-strong)]">
        <Sparkles className="h-7 w-7" />
      </div>
      <h2 className="mt-4 text-lg font-semibold text-[color:var(--ink-strong)]">Ready to learn</h2>
      <p className="mt-1.5 max-w-xs text-sm leading-relaxed text-[color:var(--ink-muted)]">
        Press <strong>Start</strong> to open your first lesson. The teacher will guide you through the material step by step.
      </p>
    </div>
  );
}
