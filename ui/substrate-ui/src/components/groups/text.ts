/** A person's colour in a group, the same every time: a hue from their name, so the same agent is the same colour everywhere. */
export function hueOf(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

export function initialOf(name: string): string {
  return name.trim().charAt(0).toUpperCase() || "?";
}

export type Piece = { text: string; mention: boolean };

/** A message cut into plain text and the `@Name` / `@everyone` parts the group highlights. */
export function splitMentions(text: string, names: string[]): Piece[] {
  const alternatives = ["everyone", ...names].map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`@(?:${alternatives.join("|")})(?!\\w)`, "gi");
  const pieces: Piece[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) pieces.push({ text: text.slice(last, match.index), mention: false });
    pieces.push({ text: match[0], mention: true });
    last = match.index + match[0].length;
  }
  if (last < text.length) pieces.push({ text: text.slice(last), mention: false });
  return pieces;
}

/** The `@word` being typed at the end of `before` (the text up to the caret), or `null` when the caret is not in one. */
export function mentionQuery(before: string): string | null {
  const match = /(?:^|\s)@([\w.-]*)$/.exec(before);
  return match ? match[1] : null;
}

/** Who to offer while a mention is typed: `@everyone` and the members whose name starts with what was typed. */
export function mentionChoices(query: string, names: string[]): string[] {
  const q = query.toLowerCase();
  return ["everyone", ...names].filter((n) => n.toLowerCase().startsWith(q));
}

/** "Scout is typing…", "Scout and Quill are typing…", "Scout, Quill and Max are typing…". */
export function typingLine(names: string[]): string {
  if (names.length === 0) return "";
  if (names.length === 1) return `${names[0]} is typing…`;
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} are typing…`;
}
