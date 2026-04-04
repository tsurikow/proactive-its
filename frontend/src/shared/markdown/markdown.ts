export function preprocessMarkdown(content: string): string {
  return stripInternalRefNoise(
    normalizeAdmonitionBlocks(
      normalizeLatexDelimiters(convertHtmlImagesToMarkdown(content)),
    ),
  );
}

function normalizeLatexDelimiters(input: string): string {
  return input
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, math) => `$${math}$`)
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `$$${math}$$`);
}

function stripInternalRefNoise(input: string): string {
  return input
    .replace(/\(\s*see\s+fs-id[0-9a-z-]+\s*\)/gi, "")
    .replace(/\bsee\s+fs-id[0-9a-z-]+\b/gi, "")
    .replace(/\n{3,}/g, "\n\n");
}

function convertHtmlImagesToMarkdown(input: string): string {
  return input.replace(/<img\b[^>]*>/gi, (imgTag) => {
    const srcMatch = imgTag.match(/\bsrc\s*=\s*(['"])(.*?)\1/i);
    if (!srcMatch || !srcMatch[2]) {
      return "";
    }
    const altMatch = imgTag.match(/\balt\s*=\s*(['"])(.*?)\1/i);
    const altText = (altMatch?.[2] ?? "").replace(/\]/g, "\\]");
    return `![${altText}](${srcMatch[2]})`;
  });
}

const ADMONITION_LABEL_RE =
  /^(?:\*\*)?(Checkpoint|Hint|Hints|Example|Examples|Worked Example|Try It|Practice|Remember|Key Idea|Key Takeaway)(?:\*\*)?(?:\s*[:.]\s*|\s*$)/i;

function normalizeAdmonitionBlocks(input: string): string {
  const blocks = input.split(/\n{2,}/);
  const normalized = blocks.map((block) => {
    const trimmed = block.trim();
    if (!trimmed || trimmed.startsWith(">")) {
      return block;
    }

    const lines = trimmed.split("\n");
    const first = lines.find((line) => line.trim().length > 0)?.trim() ?? "";
    if (!ADMONITION_LABEL_RE.test(first)) {
      return block;
    }

    if (lines.some((line) => /^#{1,6}\s/.test(line.trim()))) {
      return block;
    }

    return lines.map((line) => (line.trim() ? `> ${line}` : ">")).join("\n");
  });

  return normalized.join("\n\n");
}
