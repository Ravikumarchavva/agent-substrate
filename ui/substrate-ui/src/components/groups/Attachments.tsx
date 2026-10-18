import { Download } from "lucide-react";
import { cn } from "@/design";
import { buildObjectUrl } from "@/lib/api/_client";
import type { GroupFile } from "@/lib/api/groups";
import { FileTypeIcon } from "@/components/FileTypeIcon";
import { fileMeta, snippetOf } from "./text";

const isImage = (f: GroupFile) => f.mime.startsWith("image/");

/** A file in a message: a picture shown as one, anything else as a card with its type, name and size. Opens in a new tab. */
export function Attachment({ file, className }: { file: GroupFile; className?: string }) {
  const url = buildObjectUrl(file.key);
  if (isImage(file)) {
    return (
      <a href={url} target="_blank" rel="noreferrer" className={cn("block overflow-hidden rounded-lg", className)} aria-label={`Open ${file.name}`}>
        {/* eslint-disable-next-line @next/next/no-img-element -- an authenticated app URL, not a static asset */}
        <img src={url} alt={file.name} loading="lazy" className="max-h-72 w-full object-cover" />
      </a>
    );
  }
  const preview = file.preview_key ? buildObjectUrl(file.preview_key) : null;
  const snippet = !preview && file.excerpt ? snippetOf(file.excerpt) : null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      download={file.name}
      className={cn("block overflow-hidden rounded-lg bg-background/60 transition-colors hover:bg-background", className)}
    >
      {preview && (
        <span className="block max-h-56 overflow-hidden bg-card">
          {/* eslint-disable-next-line @next/next/no-img-element -- an authenticated app URL, not a static asset */}
          <img src={preview} alt={`First page of ${file.name}`} loading="lazy" className="w-full object-cover object-top" />
        </span>
      )}
      {snippet && (
        <span className="relative block max-h-28 overflow-hidden bg-card px-3 py-2">
          <span className="block whitespace-pre-wrap break-all font-mono text-2xs leading-4 text-muted">{snippet}</span>
          <span className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-linear-to-t from-card to-transparent" aria-hidden />
        </span>
      )}
      <span className="flex items-center gap-3 p-2">
        <FileTypeIcon name={file.name} mime={file.mime} size="md" as="span" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">{file.name}</span>
          <span className="block truncate text-xs text-muted">{fileMeta(file)}</span>
        </span>
        <Download className="size-4 shrink-0 text-muted" aria-hidden />
      </span>
    </a>
  );
}

/** All the files of one message: pictures in a grid, the rest stacked. */
export function Attachments({ files }: { files: GroupFile[] }) {
  if (files.length === 0) return null;
  const pictures = files.filter(isImage);
  const others = files.filter((f) => !isImage(f));
  return (
    <div className="mb-1 space-y-1.5">
      {pictures.length > 0 && (
        <div className={cn("grid gap-1", pictures.length > 1 && "grid-cols-2")}>
          {pictures.map((f) => (
            <Attachment key={f.key} file={f} />
          ))}
        </div>
      )}
      {others.map((f) => (
        <Attachment key={f.key} file={f} />
      ))}
    </div>
  );
}
