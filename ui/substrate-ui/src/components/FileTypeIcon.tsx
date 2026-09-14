import { getFileGlyph } from "@/lib/file-utils";

const SIZE_PX = { sm: 28, md: 36, lg: 48, xl: 64 } as const;
export type FileTypeIconSize = keyof typeof SIZE_PX;

/**
 * The one file/folder icon every surface in the app renders — a flat,
 * solid-color square with a white glyph, one color per file type (see
 * getFileGlyph in lib/file-utils.ts, the single source of truth for which
 * color/icon a given file gets). Replaces the previous mix of plain lucide
 * icons, soft-tint color badges, and StorageTab's own folded-page shape —
 * all real, all different, all in different spots (MessageBubble, the
 * composer, AppPanel's tab bar, StorageTab). One component, one mapping.
 */
export function FileTypeIcon({
  name,
  mime,
  size = "md",
  className = "",
}: {
  name: string;
  mime?: string;
  size?: FileTypeIconSize;
  className?: string;
}) {
  const { Icon, bg } = getFileGlyph(name, mime);
  const px = SIZE_PX[size];
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-[28%] ${bg} ${className}`}
      style={{ width: px, height: px }}
    >
      <Icon
        className="text-white"
        style={{ width: px * 0.52, height: px * 0.52 }}
        strokeWidth={2}
      />
    </div>
  );
}
