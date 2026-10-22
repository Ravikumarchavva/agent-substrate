import { useEffect, useRef, useState } from "react";

/**
 * Accept files dropped anywhere on the page. Returns whether a file is being dragged over the window right now, so the caller can show a
 * "drop here" overlay. Only drags that carry files count (dragging selected text does nothing), and `enabled` turns it off while the
 * page can't take an upload.
 */
export function useFileDrop(onFiles: (files: File[]) => void, enabled = true): boolean {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0); // dragenter/leave fire for every child element; count them so the overlay doesn't flicker
  const handler = useRef(onFiles);
  useEffect(() => {
    handler.current = onFiles;
  });

  useEffect(() => {
    if (!enabled) return;
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth.current += 1;
      setDragging(true);
    };
    const over = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault(); // required, or the browser opens the file instead of dropping it
    };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setDragging(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) handler.current(files);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [enabled]);

  return enabled && dragging;
}
