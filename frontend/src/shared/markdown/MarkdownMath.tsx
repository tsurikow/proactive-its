import { memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import { preprocessMarkdown } from "./markdown";
import { markdownComponents } from "./renderShared";

const remarkPlugins = [remarkGfm, remarkMath];
const rehypePlugins = [rehypeKatex];

export interface MarkdownMathProps {
  content: string;
  className?: string;
}

export const MarkdownMath = memo(function MarkdownMath({ content, className }: MarkdownMathProps) {
  const normalized = useMemo(() => preprocessMarkdown(content), [content]);
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={markdownComponents}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
});
