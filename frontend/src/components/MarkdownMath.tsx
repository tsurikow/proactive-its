import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { preprocessMarkdown } from "../lib/markdown";
import { markdownComponents } from "./markdownRenderShared";

export interface MarkdownMathProps {
  content: string;
  className?: string;
}

export function MarkdownMath({ content, className }: MarkdownMathProps) {
  const normalized = useMemo(() => preprocessMarkdown(content), [content]);
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={markdownComponents}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
