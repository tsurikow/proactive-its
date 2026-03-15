import { useEffect, useRef } from "react";
import { BookOpen, Lightbulb, LoaderCircle, MessageCircle, Send } from "lucide-react";

import { MarkdownMath } from "../../components/MarkdownMath";
import type { FeedMessage } from "./types";
import type { PendingStatus } from "../session/types";

interface ChatPaneProps {
  currentTitle: string;
  currentBreadcrumb: string;
  messages: FeedMessage[];
  loading: boolean;
  pendingStatus: PendingStatus | null;
  focusMessageId: string | null;
  onFocusedMessage: () => void;
  hasLearner: boolean;
  chatInput: string;
  onChatInputChange: (value: string) => void;
  onSend: () => Promise<void>;
}

export function ChatPane({
  currentTitle,
  currentBreadcrumb,
  messages,
  loading,
  pendingStatus,
  focusMessageId,
  onFocusedMessage,
  hasLearner,
  chatInput,
  onChatInputChange,
  onSend,
}: ChatPaneProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const isNearBottomRef = useRef(true);
  const messageRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !isNearBottomRef.current || focusMessageId) {
      return;
    }
    root.scrollTo({ top: root.scrollHeight, behavior: "smooth" });
  }, [messages, focusMessageId]);

  useEffect(() => {
    if (!focusMessageId) {
      return;
    }
    const root = scrollRef.current;
    const target = messageRefs.current[focusMessageId];
    if (!root || !target) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const rootRect = root.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const offsetTop = targetRect.top - rootRect.top + root.scrollTop - 16;
      root.scrollTo({ top: Math.max(0, offsetTop), behavior: "smooth" });
      onFocusedMessage();
    });
    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [focusMessageId, messages.length, onFocusedMessage]);

  return (
    <main className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-white/50">
      <div
        ref={scrollRef}
        onScroll={(event) => {
          const root = event.currentTarget;
          isNearBottomRef.current = root.scrollHeight - root.scrollTop - root.clientHeight <= 100;
        }}
        className="flex-1 overflow-y-auto px-4 pb-32 pt-4 sm:px-8 sm:pt-8 lg:px-12 lg:pt-10"
      >
        <div className="mx-auto max-w-3xl space-y-8">
          <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-teal-700">
              <BookOpen className="h-4 w-4" />
              Current Section
            </div>
            <h2 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">{currentTitle}</h2>
            <p className="mt-3 text-sm leading-relaxed text-slate-600">{currentBreadcrumb}</p>
          </section>

          {messages.length === 0 ? (
            <section className="rounded-2xl border border-dashed border-slate-300 bg-white/80 p-8 text-center text-slate-500">
              Click <strong>Start Session</strong> to begin your tutor flow.
            </section>
          ) : (
            messages.map((message) => (
              <section
                key={message.id}
                ref={(node) => {
                  messageRefs.current[message.id] = node;
                }}
                className={`rounded-2xl border p-5 shadow-sm ${
                  message.role === "user"
                    ? "ml-8 border-teal-200 bg-teal-50/60"
                    : message.role === "system"
                      ? "border-indigo-200 bg-indigo-50/50"
                      : message.role === "error"
                        ? "border-rose-200 bg-rose-50/70"
                        : "border-slate-200 bg-white"
                }`}
              >
                {message.title ? (
                  <h3 className="mb-3 text-sm font-bold uppercase tracking-wider text-slate-600">
                    {message.title}
                  </h3>
                ) : null}
                <MarkdownMath
                  content={message.content}
                  className="prose prose-slate max-w-none text-slate-700"
                />
              </section>
            ))
          )}

          {pendingStatus ? (
            <section
              className={`rounded-2xl border p-5 shadow-sm ${
                pendingStatus.kind === "answer"
                  ? "border-slate-200 bg-white"
                  : "border-indigo-200 bg-indigo-50/50"
              }`}
            >
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-600">
                <LoaderCircle className="h-4 w-4 animate-spin text-teal-600" />
                {pendingStatus.kind === "answer" ? "Tutor" : "Lesson"}
              </div>
              <div className="flex items-center gap-3 text-slate-700">
                <span>{pendingStatus.text}</span>
                <span className="loading-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
              </div>
            </section>
          ) : null}

          <section className="rounded-2xl border border-teal-100 bg-teal-50 p-5">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-teal-800">
              <Lightbulb className="h-4 w-4" />
              Key Takeaway
            </div>
            <p className="text-sm text-teal-900">
              Ask about any step from the current section, and I will answer with grounded evidence.
            </p>
          </section>
        </div>
      </div>

      <div className="absolute bottom-0 left-0 w-full bg-gradient-to-t from-[#F8FAFC] via-[#F8FAFC]/90 to-transparent px-4 pb-6 pt-10 sm:px-8">
        <div className="mx-auto max-w-3xl">
          {pendingStatus ? (
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-teal-200 bg-white/90 px-3 py-1.5 text-xs font-semibold text-teal-700 shadow-sm">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              {pendingStatus.text}
            </div>
          ) : null}
          <div className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-lg transition-all focus-within:border-teal-500 focus-within:ring-2 focus-within:ring-teal-500/20">
            <div className="p-3 text-slate-400">
              <MessageCircle className="h-6 w-6" />
            </div>
            <textarea
              rows={1}
              placeholder="Ask the tutor to explain a step, or type a math question..."
              className="max-h-32 flex-1 resize-none bg-transparent py-3 leading-relaxed text-slate-700 outline-none placeholder:text-slate-400"
              value={chatInput}
              disabled={loading || !hasLearner}
              onChange={(event) => onChatInputChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void onSend();
                }
              }}
            />
            <button
              onClick={() => void onSend()}
              disabled={!chatInput.trim() || loading || !hasLearner}
              className={`rounded-xl p-3 transition-all ${
                chatInput.trim() && !loading && hasLearner
                  ? "bg-teal-600 text-white shadow-sm hover:bg-teal-700"
                  : "cursor-not-allowed bg-slate-100 text-slate-400"
              }`}
              aria-label="Send message"
            >
              <Send className="h-5 w-5" />
            </button>
          </div>
          <div className="mt-3 text-center text-xs font-medium text-slate-400">
            AI Tutor can make mistakes. Check important math steps.
          </div>
        </div>
      </div>
    </main>
  );
}
