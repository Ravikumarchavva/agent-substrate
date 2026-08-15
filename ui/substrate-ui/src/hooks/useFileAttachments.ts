"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import type { Thread, UploadedFile } from "@/types";
import { type AttachmentKind, getFileExtension, getAttachmentKind } from "@/lib/file-utils";
import { api } from "@/lib/api";
import { useToast } from "@/contexts/ToastContext";

// PDFs and pasted-text documents go through eager staged extraction+
// embedding today (see EXTRACTABLE_CONTENT_TYPES in agent-substrate's
// chat_context.py) — other types never get staged_at/staging_error and
// don't need polling. text/markdown is how a large paste becomes a
// document (see the composer's paste handler) — it skips OCR entirely
// (local TextLoader, not the extraction service) so staging is fast, but
// it's still staged_at/staging_error-bearing, so it still needs polling.
const STAGED_MIME_TYPES = new Set(["application/pdf", "text/markdown"]);

// This session's own measured baseline for the "tiny" OCR variant (see
// agent-substrate's extraction pipeline benchmarking) — used only to
// simulate a progress ring's fill rate, since true per-page progress isn't
// available (PPStructureV3 batches internally despite predict_iter()
// looking lazy). Real completion is still gated on staged_at, not this
// estimate — see computeSimulatedProgress below.
const EXPECTED_SECONDS_PER_PAGE = 11.5;
const STATUS_POLL_INTERVAL_MS = 1500;

export type AttachedFilePreview = UploadedFile & {
  extension: string;
  previewKind: AttachmentKind;
  previewUrl?: string;
  createdAt?: string;
  pageCount?: number | null;
  stagedAt?: string | null;
  stagingError?: string | null;
};

export type AttachmentProcessingState = "not-applicable" | "pending" | "ready" | "error";

export function getAttachmentProcessingState(file: AttachedFilePreview): AttachmentProcessingState {
  if (!STAGED_MIME_TYPES.has(file.mime)) return "not-applicable";
  if (file.stagingError) return "error";
  if (file.stagedAt) return "ready";
  return "pending";
}

/** Simulated progress (0-1) — an asymptotic curve (never a hard linear ETA)
 * approaching, but never reaching, a cap below 100% until staged_at
 * actually confirms completion (see the module doc comment above for why
 * this can't be real progress).
 *
 * Deliberately NOT `elapsed / estimatedTotal` capped at a ceiling: real
 * processing regularly takes longer than EXPECTED_SECONDS_PER_PAGE's
 * benchmark baseline (different hardware, cold model cache, larger/denser
 * pages), and a linear estimate hits its cap fast, then sits visibly
 * "stuck" for however much longer the real work takes — reads as broken.
 * An exponential approach keeps crawling forward for as long as it takes,
 * slowing down but never stopping, which stays honest regardless of how
 * wrong the per-page estimate turns out to be for a given document. */
export function computeSimulatedProgress(file: AttachedFilePreview): number {
  if (file.stagedAt) return 1;
  if (!file.createdAt) return 0;
  const elapsedSeconds = (Date.now() - new Date(file.createdAt).getTime()) / 1000;
  const estimatedTotal = EXPECTED_SECONDS_PER_PAGE * (file.pageCount ?? 1);
  if (estimatedTotal <= 0) return 0;
  const cap = 0.97;
  return cap * (1 - Math.exp(-elapsedSeconds / estimatedTotal));
}

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
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFilePreview[]>([]);
  const [uploadingFile, setUploadingFile] = useState(false);
  const attachedFilesRef = useRef<AttachedFilePreview[]>([]);
  // waitForAttachmentsReady's poll intervals — tracked so unmount can clear
  // any still-running one rather than leaking it (a send queued right
  // before navigating away would otherwise poll forever).
  const waitIntervalsRef = useRef<Set<ReturnType<typeof setInterval>>>(new Set());

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

  // Revoke object URLs and clear any outstanding wait-for-ready poll on unmount
  useEffect(() => {
    const waitIntervals = waitIntervalsRef.current;
    return () => {
      attachedFilesRef.current.forEach(revokeAttachedFilePreview);
      waitIntervals.forEach(clearInterval);
      waitIntervals.clear();
    };
  }, []);

  // Fetch + merge status for the given file ids — shared by the immediate
  // post-upload poll (handleFileSelected) and the recurring interval below.
  const pollFileIds = useCallback(async (fileIds: string[]) => {
    if (!fileIds.length) return;
    await Promise.all(
      fileIds.map(async (fileId) => {
        const status = await api.getFileStatus(fileId);
        if (!status) return;
        setAttachedFiles((prev) =>
          prev.map((file) =>
            file.id === fileId
              ? {
                  ...file,
                  createdAt: status.created_at,
                  pageCount: status.page_count,
                  stagedAt: status.staged_at,
                  stagingError: status.staging_error,
                }
              : file
          )
        );
      })
    );
  }, []);

  // Poll status for any attachment still processing (staged_at/staging_error
  // unset) — drives the composer's per-attachment progress ring and the
  // queued-send wait. One shared interval rather than one per file; reads
  // attachedFilesRef so it doesn't need to re-register on every state
  // change.
  useEffect(() => {
    const id = setInterval(() => {
      const pending = attachedFilesRef.current.filter(
        (f) => STAGED_MIME_TYPES.has(f.mime) && !f.stagedAt && !f.stagingError
      );
      void pollFileIds(pending.map((f) => f.id));
    }, STATUS_POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [pollFileIds]);

  // Shared by handleFileSelected (the [+] button / native file picker) and
  // handleFilesPasted (paste-to-document, see the composer's paste handler)
  // — both just need to get File[] into the exact same upload+poll+error
  // path; only how the File[] is obtained differs.
  const uploadFiles = useCallback(async (files: File[]) => {
    if (!files.length) return;

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
        showToast("Could not start a new chat for this upload.", "error");
        return;
      }
    }

    setUploadingFile(true);
    try {
      const uploaded = await Promise.all(
        files.map(async (file) => createAttachedFilePreview(await api.uploadFile(threadId!, file), file))
      );
      setAttachedFiles((prev) => [...prev, ...uploaded]);

      // Immediate poll for staged types — don't wait for the first interval
      // tick just to learn created_at/page_count (needed for the progress
      // ring's estimate) or, on a small/fast doc, staged_at itself.
      const stagedIds = uploaded
        .filter((f) => STAGED_MIME_TYPES.has(f.mime))
        .map((f) => f.id);
      void pollFileIds(stagedIds);
    } catch (err) {
      // Surface the real backend message (e.g. "Document has 128 pages,
      // exceeding the 20-page limit.") to the user, not just a console log
      // — a silent failure here looks like nothing happened.
      const message = err instanceof Error ? err.message : "File upload failed.";
      showToast(message, "error");
    } finally {
      setUploadingFile(false);
    }
  }, [currentThreadId, promoteThreadUrl, setThreads, pollFileIds, showToast]);

  const handleFileSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    // Reset so the same file can be re-selected
    e.target.value = "";
    await uploadFiles(files);
  }, [uploadFiles]);

  // Paste-to-document: the composer synthesizes a text/markdown File from
  // pasted content over the length threshold and hands it here — reuses
  // the exact same upload/staging/promote pipeline a manual attach does,
  // no separate code path.
  const handleFilesPasted = useCallback(async (files: File[]) => {
    await uploadFiles(files);
  }, [uploadFiles]);

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

  // Queued send: called on send-button click. Resolves immediately if
  // nothing is still processing; otherwise waits (checking local state
  // already kept fresh by the background poll above — no extra network
  // calls here) until every attachment is ready, or bails out the moment
  // any of them reports a staging_error, so the composer isn't stuck
  // waiting on a file that will never finish. Removing the offending
  // attachment (handleRemoveFile) while a send is queued lets a retry
  // succeed without the error blocking forever.
  const waitForAttachmentsReady = useCallback((): Promise<
    { ok: true } | { ok: false; error: string }
  > => {
    return new Promise((resolve) => {
      const check = () => {
        const files = attachedFilesRef.current;
        const failed = files.find(
          (f) => getAttachmentProcessingState(f) === "error"
        );
        if (failed) {
          resolve({ ok: false, error: `'${failed.name}' failed to process: ${failed.stagingError}` });
          return true;
        }
        const stillPending = files.some(
          (f) => getAttachmentProcessingState(f) === "pending"
        );
        if (!stillPending) {
          resolve({ ok: true });
          return true;
        }
        return false;
      };

      if (check()) return;
      const id = setInterval(() => {
        if (check()) {
          clearInterval(id);
          waitIntervalsRef.current.delete(id);
        }
      }, 250);
      waitIntervalsRef.current.add(id);
    });
  }, []);

  return {
    attachedFiles,
    uploadingFile,
    fileInputRef,
    clearAttachedFiles,
    handleFileSelected,
    handleFilesPasted,
    handleRemoveFile,
    waitForAttachmentsReady,
  };
}
