import type { ComponentPropsWithoutRef } from "react";
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { preprocessMarkdown } from "../lib/markdown";

interface MarkdownMathProps {
  content: string;
  className?: string;
}

const MEDIA_BASE_URL = (() => {
  const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/v1";
  if (apiBaseUrl.startsWith("http://") || apiBaseUrl.startsWith("https://")) {
    try {
      return new URL(apiBaseUrl).origin;
    } catch {
      return "";
    }
  }
  return "";
})();

function resolveImageSrc(src: string): string {
  if (!src) {
    return src;
  }
  if (/^(https?:)?\/\//i.test(src) || src.startsWith("data:")) {
    return src;
  }
  if (src.startsWith("media/")) {
    return `${MEDIA_BASE_URL}/media/${src.slice("media/".length)}`;
  }
  if (src.startsWith("/media/")) {
    return `${MEDIA_BASE_URL}${src}`;
  }
  return src;
}

function isFigureLink(href: string): boolean {
  if (!href) {
    return false;
  }
  const cleaned = href.toLowerCase().split("?")[0].split("#")[0];
  return (
    cleaned.startsWith("media/") ||
    cleaned.startsWith("/media/") ||
    /\.(png|jpg|jpeg|gif|webp|svg)$/.test(cleaned)
  );
}

export function MarkdownMath({ content, className }: MarkdownMathProps) {
  const normalized = useMemo(() => preprocessMarkdown(content), [content]);
  try {
    return (
      <ReactMarkdown
        className={className}
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          blockquote: (props: ComponentPropsWithoutRef<"blockquote">) => (
            <blockquote
              {...props}
              className="my-6 rounded-2xl border border-teal-200 bg-gradient-to-br from-teal-50/90 to-white px-5 py-4 text-slate-700 shadow-sm"
            />
          ),
          table: (props: ComponentPropsWithoutRef<"table">) => (
            <div className="markdown-table-wrap">
              <table {...props} />
            </div>
          ),
          img: (props: ComponentPropsWithoutRef<"img">) => (
            <img
              {...props}
              src={resolveImageSrc(props.src ?? "")}
              loading="lazy"
              className="markdown-image"
            />
          ),
          a: (props: ComponentPropsWithoutRef<"a">) => {
            const href = props.href ?? "";
            if (isFigureLink(href)) {
              return (
                <a {...props} href={resolveImageSrc(href)} target="_blank" rel="noreferrer" />
              );
            }
            return <span>{props.children}</span>;
          },
        }}
      >
        {normalized}
      </ReactMarkdown>
    );
  } catch {
    return <pre className={className}>{content}</pre>;
  }
}
