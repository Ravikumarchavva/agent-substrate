"use client";

import { useState } from "react";
import { Camera, Trash2 } from "lucide-react";
import { Button, FilePicker, toast } from "@/design";
import { Avatar } from "@/components/groups/Avatar";
import { reportError } from "@/lib/report-error";

/** An agent's or a group's picture, with the buttons to change or remove it. `save` and `clear` return once the change is stored. */
export function PictureEditor({
  name,
  src,
  save,
  clear,
  className,
}: {
  name: string;
  src: string | null;
  save: (file: File) => Promise<void>;
  clear: () => Promise<void>;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const run = async (work: () => Promise<void>, failure: string) => {
    setBusy(true);
    try {
      await work();
    } catch (err) {
      reportError(failure, err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={className}>
      <Avatar name={name} src={src} className="size-20 text-3xl" />
      <div className="flex flex-wrap items-center gap-2">
        <FilePicker
          multiple={false}
          accept="image/png,image/jpeg,image/webp"
          variant="ghost"
          size="sm"
          disabled={busy}
          onFiles={([file]) => {
            if (file.size > 5 * 1024 * 1024) return toast.error("That picture is larger than 5 MB.");
            void run(() => save(file), "Couldn't set the picture");
          }}
        >
          <Camera /> {src ? "Change picture" : "Add picture"}
        </FilePicker>
        {src && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => void run(clear, "Couldn't remove the picture")}>
            <Trash2 /> Remove
          </Button>
        )}
      </div>
    </div>
  );
}
