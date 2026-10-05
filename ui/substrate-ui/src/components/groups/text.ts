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

/** A message's day as a chat shows it between runs of messages: "Today", "Yesterday", the weekday this week, else the date. */
export function dayLabel(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(at)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return at.toLocaleDateString([], { weekday: "long" });
  return at.toLocaleDateString([], { month: "long", day: "numeric", year: at.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

/** Whether two moments fall on the same calendar day. */
export function sameDay(a: string, b: string): boolean {
  const x = new Date(a);
  const y = new Date(b);
  return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth() && x.getDate() === y.getDate();
}

/** A file size as people read it: 512 B, 1.5 KB, 3.2 MB. */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** What a chat list shows for a message: its text, or the files when there is none ("📎 budget.xlsx", "📎 3 files"). */
export function previewOf(text: string, attachments: { name: string }[]): string {
  if (text.trim()) return text;
  if (attachments.length === 1) return `📎 ${attachments[0].name}`;
  return attachments.length > 1 ? `📎 ${attachments.length} files` : "";
}

/** What a file card says under its name: "2 pages • PDF • 104 kB". */
export function fileMeta(file: { name: string; size: number; pages?: number | null }): string {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toUpperCase() : "FILE";
  return [file.pages ? `${file.pages} page${file.pages === 1 ? "" : "s"}` : null, ext, fileSize(file.size)].filter(Boolean).join(" • ");
}

/** The first few lines of a file's text, for the card of a file that has no picture. */
export function snippetOf(excerpt: string, lines = 6): string {
  return excerpt.split("\n").slice(0, lines).join("\n");
}
