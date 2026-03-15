import type { ComponentPropsWithoutRef } from "react";

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

export function resolveImageSrc(src: string): string {
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

export function isFigureLink(href: string): boolean {
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

export const markdownComponents = {
  blockquote: ({ node: _node, ...props }: ComponentPropsWithoutRef<"blockquote"> & { node?: unknown }) => (
    <blockquote
      {...props}
      className="my-6 rounded-2xl border border-teal-200 bg-gradient-to-br from-teal-50/90 to-white px-5 py-4 text-slate-700 shadow-sm"
    />
  ),
  table: ({ node: _node, ...props }: ComponentPropsWithoutRef<"table"> & { node?: unknown }) => (
    <div className="markdown-table-wrap">
      <table {...props} />
    </div>
  ),
  img: ({ node: _node, ...props }: ComponentPropsWithoutRef<"img"> & { node?: unknown }) => (
    <img
      {...props}
      src={resolveImageSrc(props.src ?? "")}
      loading="lazy"
      className="markdown-image"
    />
  ),
  a: ({ node: _node, ...props }: ComponentPropsWithoutRef<"a"> & { node?: unknown }) => {
    const href = props.href ?? "";
    if (isFigureLink(href)) {
      return <a {...props} href={resolveImageSrc(href)} target="_blank" rel="noreferrer" />;
    }
    return <span>{props.children}</span>;
  },
};
