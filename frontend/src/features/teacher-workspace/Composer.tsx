import { Send } from "lucide-react";

import type { ComposerState } from "../session/types";

export function Composer({ composer }: { composer: ComposerState }) {
  return (
    <div className="border-t border-[color:var(--line-soft)] bg-[color:var(--surface)] px-3 pb-[env(safe-area-inset-bottom,0.5rem)] pt-2 sm:px-4">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          rows={1}
          value={composer.value}
          onChange={(event) => composer.setValue(event.target.value)}
          disabled={composer.disabled}
          placeholder="Message your teacher..."
          className="max-h-32 min-h-[2.75rem] flex-1 resize-none rounded-[var(--radius-lg)] border border-[color:var(--line-soft)] bg-[color:var(--surface-soft)] px-3.5 py-2.5 text-[15px] leading-6 text-[color:var(--ink-strong)] outline-none transition placeholder:text-[color:var(--ink-soft)] focus:border-[color:var(--accent-strong)] focus:ring-1 focus:ring-[color:var(--accent-strong)]"
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void composer.send();
            }
          }}
        />
        <button
          type="button"
          onClick={() => void composer.send()}
          disabled={!composer.value.trim() || composer.disabled}
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--radius-lg)] bg-[color:var(--accent-strong)] text-white transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Send message"
        >
          <Send className="h-4.5 w-4.5" />
        </button>
      </div>
    </div>
  );
}
