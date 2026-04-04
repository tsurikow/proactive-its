import { Sparkles } from "lucide-react";

export function TypingIndicator({ text }: { text: string }) {
  return (
    <div className="max-w-[85%] rounded-[var(--radius-lg)] border border-[color:var(--line-soft)] bg-[color:var(--surface)] px-4 py-3">
      <div className="flex items-center gap-2">
        <div className="flex h-5 w-5 items-center justify-center rounded-full bg-[color:var(--accent-strong)] text-white">
          <Sparkles className="h-3 w-3" />
        </div>
        <span className="text-xs font-medium text-[color:var(--ink-muted)]">Teacher</span>
      </div>
      <div className="mt-2 flex items-center gap-2 text-sm text-[color:var(--ink-soft)]">
        <span className="loading-dots">
          <span /><span /><span />
        </span>
        {text}
      </div>
    </div>
  );
}
