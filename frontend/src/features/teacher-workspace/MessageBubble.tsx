import { useCallback, useState } from "react";
import { Check, Copy, Sparkles } from "lucide-react";

import { MarkdownMath } from "../../shared/markdown/MarkdownMath";
import type { FeedMessage } from "../session/messages";
import { CheckpointCard } from "./CheckpointCard";
import { CitationList } from "./CitationList";

export function MessageBubble({ message }: { message: FeedMessage }) {
  return (
    <article className={messageCardClass(message)}>
      {message.role !== "user" ? (
        <div className="flex items-start justify-between gap-2">
          <MessageHeader message={message} />
          {message.role !== "error" ? <CopyButton content={message.content} /> : null}
        </div>
      ) : null}
      <MarkdownMath
        content={message.content}
        className="prose prose-slate max-w-none text-[color:var(--ink-strong)]"
      />
      {message.checkpointEvaluation ? <CheckpointCard evaluation={message.checkpointEvaluation} /> : null}
      {message.citations?.length ? <CitationList citations={message.citations} /> : null}
    </article>
  );
}

function MessageHeader({ message }: { message: FeedMessage }) {
  if (message.role === "error") {
    return <div className="mb-2 text-xs font-medium text-rose-600">Error</div>;
  }
  return (
    <div className="mb-2 flex items-center gap-1.5">
      <div className="flex h-5 w-5 items-center justify-center rounded-full bg-[color:var(--accent-strong)] text-white">
        <Sparkles className="h-3 w-3" />
      </div>
      <span className="text-xs font-medium text-[color:var(--ink-muted)]">
        {message.title || "Teacher"}
      </span>
    </div>
  );
}

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [content]);

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)] text-[color:var(--ink-soft)] opacity-0 transition hover:bg-[color:var(--surface-soft)] hover:text-[color:var(--ink-muted)] group-hover:opacity-100 focus:opacity-100"
      aria-label="Copy message"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

function messageCardClass(message: FeedMessage): string {
  const base = "px-4 py-3 rounded-[var(--radius-lg)]";
  if (message.role === "user") {
    return `ml-auto max-w-[85%] ${base} bg-[color:var(--accent-soft)] text-[color:var(--ink-strong)]`;
  }
  if (message.role === "error") {
    return `max-w-[85%] ${base} border border-rose-200 bg-rose-50`;
  }
  if (message.checkpointEvaluation) {
    return `group max-w-[85%] ${base} border border-amber-200 bg-[color:var(--surface)]`;
  }
  return `group max-w-[85%] ${base} border border-[color:var(--line-soft)] bg-[color:var(--surface)]`;
}
