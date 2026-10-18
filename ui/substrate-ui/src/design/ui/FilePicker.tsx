"use client";

import { useRef, type ReactNode } from "react";
import { Button, type ButtonProps } from "./Button";

/**
 * A button that opens the system file chooser. Hands back the files picked, then forgets them, so choosing the same file twice in a row
 * still counts. Anything that takes files (a composer, an uploader) uses this instead of a raw `<input type="file">`.
 */
export function FilePicker({ onFiles, accept, multiple = true, children, ...button }: { onFiles: (files: File[]) => void; accept?: string; multiple?: boolean; children: ReactNode } & Omit<ButtonProps, "onClick" | "children">) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button {...button} onClick={() => input.current?.click()}>
        {children}
      </Button>
      <input
        ref={input}
        type="file"
        hidden
        multiple={multiple}
        accept={accept}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length > 0) onFiles(files);
        }}
      />
    </>
  );
}
