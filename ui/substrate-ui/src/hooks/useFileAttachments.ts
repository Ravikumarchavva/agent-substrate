"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import type { Thread, UploadedFile } from "@/types";
import { type AttachmentKind, getFileExtension, getAttachmentKind } from "@/lib/file-utils";
import { api } from "@/lib/api";

export type AttachedFilePreview = UploadedFile & {
  extension: string;
  previewKind: AttachmentKind;
  previewUrl?: string;
};

export function revokeAttachedFilePreview(file: AttachedFilePreview): void {
  if (file.previewUrl?.startsWith("blob:")) {
    URL.revokeObjectURL(file.previewUrl);
  }
}

function createAttachedFilePreview(file: UploadedFile, sourceFile: File): AttachedFilePreview {
  const mime = file.mime || sourceFile.type || "application/octet-stream";
  const previewKind = getAttachmentKind(mime, file.name || sourceFile.name);

  return {
    ...file,
    mime,
    extension: getFileExtension(file.name || sourceFile.name),
    previewKind,
    previewUrl: previewKind === "image" ? URL.createObjectURL(sourceFile) : undefined,
  };
}

export function useFileAttachments(
  currentThreadId: string | null,
  promoteThreadUrl: (threadId: string) => void,
  setThreads: React.Dispatch<React.SetStateAction<Thread[]>>,
) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFilePreview[]>([]);
  const [uploadingFile, setUploadingFile] = useState(false);
  const attachedFilesRef = useRef<AttachedFilePreview[]>([]);

  const clearAttachedFiles = useCallback(() => {
    setAttachedFiles((current) => {
      current.forEach(revokeAttachedFilePreview);
      return [];
    });
  }, []);

  // Keep ref in sync with state
  useEffect(() => {
    attachedFilesRef.current = attachedFiles;
  }, [attachedFiles]);

  // Revoke object URLs on unmount
  useEffect(() => {
    return () => {
      attachedFilesRef.current.forEach(revokeAttachedFilePreview);
    };
  }, []);

  const handleFileSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    // Reset so the same file can be re-selected
    e.target.value = "";

    // Ensure we have a thread before uploading
    let threadId = currentThreadId;
    if (!threadId) {
      try {
        const newThread = await api.createThread("New Chat");
        setThreads((current) => [newThread, ...current]);
        // A real Next.js navigation (router.push) here would change the
        // /chat/[[...slug]] catch-all segment and REMOUNT the page — which
        // wipes this hook's just-set attachedFiles state before the user
        // ever sees the upload succeed (see promoteThreadUrl's own comment
        // in page.tsx for the identical bug this already fixed for
        // in-flight assistant messages). Use the same remount-free URL
        // update instead.
        promoteThreadUrl(newThread.id);
        threadId = newThread.id;
      } catch {
        console.error("Failed to create thread for file upload");
        return;
      }
    }

    setUploadingFile(true);
    try {
      const uploaded = await Promise.all(
        files.map(async (file) => createAttachedFilePreview(await api.uploadFile(threadId!, file), file))
      );
      setAttachedFiles((prev) => [...prev, ...uploaded]);
    } catch (err) {
      console.error("File upload failed:", err);
    } finally {
      setUploadingFile(false);
    }
  }, [currentThreadId, promoteThreadUrl, setThreads]);

  const handleRemoveFile = useCallback(async (fileId: string) => {
    if (!currentThreadId) return;
    try {
      await api.deleteFile(currentThreadId, fileId);
    } catch {
      // Best-effort — remove from local state regardless
    }
    setAttachedFiles((prev) => {
      const removed = prev.find((file) => file.id === fileId);
      if (removed) {
        revokeAttachedFilePreview(removed);
      }
      return prev.filter((file) => file.id !== fileId);
    });
  }, [currentThreadId]);

  return {
    attachedFiles,
    uploadingFile,
    fileInputRef,
    clearAttachedFiles,
    handleFileSelected,
    handleRemoveFile,
  };
}
