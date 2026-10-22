/** Markdown reduced to what a person would say aloud: no asterisks, hashes, link syntax, citation numbers or table pipes. */
export function toSpeechText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " ") // code blocks are not read out
    .replace(/<[^>]+>/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\[\d+(?:\s*,\s*\d+)*\]/g, "") // [1] citations
    .replace(/^\s*\|?[\s:|-]{3,}\|?\s*$/gm, "") // table separator rows
    .replace(/\|/g, ", ")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "")
    .replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, "")
    .replace(/(\*\*|__|\*|_|~~|`)/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{2,}/g, "\n\n")
    .trim();
}
