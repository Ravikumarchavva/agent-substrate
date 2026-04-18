"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  X,
  Settings,
  Puzzle,
  ShieldCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import type {
  AdminStats,
  AdminStep,
  AdminThread,
  AdminUser,
  InstructionValidationResult,
  OpenAITTSVoice,
  TTSPlaybackRate,
  TTSVoice,
} from "@/types";
import {
  CHAT_MODEL_OPTIONS,
  DEFAULT_CHAT_MODEL,
  DEFAULT_REALTIME_MODEL,
  DEFAULT_REALTIME_VOICE,
  DEFAULT_STT_MODEL,
  DEFAULT_TTS_MODEL,
  DEFAULT_TTS_PLAYBACK_RATE,
  DEFAULT_TTS_VOICE,
  CHAT_MODEL_STORAGE_KEY,
  TTS_PLAYBACK_RATE_STORAGE_KEY,
  REALTIME_MODEL_OPTIONS,
  REALTIME_MODEL_STORAGE_KEY,
  REALTIME_VOICE_STORAGE_KEY,
  STT_MODEL_OPTIONS,
  STT_MODEL_STORAGE_KEY,
  TTS_MODEL_OPTIONS,
  TTS_MODEL_STORAGE_KEY,
  TTS_VOICE_STORAGE_KEY,
  clearStoredValue,
  getPreferredChatModel,
  getPreferredRealtimeModel,
  getPreferredRealtimeVoice,
  getPreferredSTTModel,
  getPreferredTTSPlaybackRate,
  getPreferredTTSModel,
  getPreferredTTSVoice,
  getVoiceOptionsForModel,
  groupModelOptions,
  writeStoredValue,
} from "@/lib/model-preferences";
import { GeneralTab } from "./GeneralTab";
import { AppsTab } from "./AppsTab";
import { AdminTab } from "./AdminTab";

export type SettingsTab = "general" | "apps" | "admin";

const CUSTOM_INSTRUCTIONS_STORAGE_KEY = "system_instructions_override";

interface SettingsNotice {
  tone: "success" | "info";
  message: string;
}

function getAsyncErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  if (typeof error === "string" && error.trim()) return error;
  return fallback;
}

interface SettingsPanelProps {
  isOpen: boolean;
  initialTab?: SettingsTab;
  onClose: () => void;
  onTabChange?: (tab: SettingsTab) => void;
}

export function SettingsPanel({
  isOpen,
  initialTab = "general",
  onClose,
  onTabChange,
}: SettingsPanelProps) {
  const {
    isAdmin,
    googleAuth,
    spotifyAuth,
    workspaceAuth,
    loginWithGoogle,
    loginWithSpotify,
    loginWithWorkspace,
    checkAuth,
  } = useAuth();

  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);
  const [customInstructions, setCustomInstructions] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [modelPreferencesNotice, setModelPreferencesNotice] = useState<SettingsNotice | null>(null);
  const [disconnectingApp, setDisconnectingApp] = useState<"google" | "spotify" | "workspace" | null>(null);

  const [chatModel, setChatModel] = useState(DEFAULT_CHAT_MODEL);
  const [sttModel, setSttModel] = useState(DEFAULT_STT_MODEL);
  const [ttsModel, setTtsModel] = useState(DEFAULT_TTS_MODEL);
  const [ttsVoice, setTtsVoice] = useState<TTSVoice>(DEFAULT_TTS_VOICE);
  const [ttsPlaybackRate, setTtsPlaybackRate] = useState<TTSPlaybackRate>(DEFAULT_TTS_PLAYBACK_RATE);
  const [realtimeModel, setRealtimeModel] = useState(DEFAULT_REALTIME_MODEL);
  const [realtimeVoice, setRealtimeVoice] = useState<OpenAITTSVoice>(DEFAULT_REALTIME_VOICE);

  const [adminThreads, setAdminThreads] = useState<AdminThread[]>([]);
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [adminStats, setAdminStats] = useState<AdminStats | null>(null);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminLoadNotice, setAdminLoadNotice] = useState<string | null>(null);
  const [adminUsersError, setAdminUsersError] = useState<string | null>(null);
  const [adminTab, setAdminTab] = useState<"users" | "threads">("users");
  const [expandedThreadId, setExpandedThreadId] = useState<string | null>(null);
  const [threadSteps, setThreadSteps] = useState<Record<string, AdminStep[]>>({});
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);

  const syncLocalSettings = useCallback(() => {
    setCustomInstructions(localStorage.getItem(CUSTOM_INSTRUCTIONS_STORAGE_KEY) ?? "");
    const preferredTtsModel = getPreferredTTSModel();
    setChatModel(getPreferredChatModel());
    setSttModel(getPreferredSTTModel());
    setTtsModel(preferredTtsModel);
    setTtsVoice(getPreferredTTSVoice(preferredTtsModel));
    setTtsPlaybackRate(getPreferredTTSPlaybackRate());
    setRealtimeModel(getPreferredRealtimeModel());
    setRealtimeVoice(getPreferredRealtimeVoice());
    setSaveError(null);
    setSaveSuccess(false);
    setModelPreferencesNotice(null);
  }, []);

  useEffect(() => { if (isOpen) syncLocalSettings(); }, [isOpen, syncLocalSettings]);
  useEffect(() => { if (isOpen) setActiveTab(initialTab); }, [isOpen, initialTab]);

  const loadAdminData = useCallback(async () => {
    setAdminLoading(true);
    setAdminLoadNotice(null);
    setAdminUsersError(null);

    try {
      const [threadsResult, usersResult, statsResult] = await Promise.allSettled([
        api.getAdminThreads(),
        api.getAdminUsers(),
        api.getAdminStats(),
      ]);

      const unavailableSections: string[] = [];

      if (threadsResult.status === "fulfilled") {
        setAdminThreads(threadsResult.value);
      } else {
        unavailableSections.push("threads");
        console.warn("Failed to load admin threads.", threadsResult.reason);
      }

      if (usersResult.status === "fulfilled") {
        setAdminUsers(usersResult.value);
      } else {
        unavailableSections.push("users");
        setAdminUsersError(getAsyncErrorMessage(usersResult.reason, "User accounts are temporarily unavailable."));
        console.warn("Failed to load admin users.", usersResult.reason);
      }

      if (statsResult.status === "fulfilled") {
        setAdminStats(statsResult.value);
      } else {
        unavailableSections.push("stats");
        console.warn("Failed to load admin stats.", statsResult.reason);
      }

      if (unavailableSections.length > 0) {
        setAdminLoadNotice(`Some admin data is unavailable right now: ${unavailableSections.join(", ")}.`);
      }
    } finally {
      setAdminLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "admin" && isAdmin && !adminLoading && adminThreads.length === 0) {
      void loadAdminData();
    }
  }, [activeTab, adminLoading, adminThreads.length, isAdmin, loadAdminData]);

  const handleSaveInstructions = async () => {
    setSaveError(null);
    setSaveSuccess(false);

    if (!customInstructions.trim()) {
      localStorage.removeItem(CUSTOM_INSTRUCTIONS_STORAGE_KEY);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
      return;
    }

    setIsSaving(true);
    try {
      const data = await api.checkCustomInstructions(customInstructions.trim()) as InstructionValidationResult;
      if (!data.allowed) {
        setSaveError(data.reason ?? "Prompt not saved: content policy violation.");
        return;
      }
      localStorage.setItem(CUSTOM_INSTRUCTIONS_STORAGE_KEY, customInstructions.trim());
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
    } catch {
      setSaveError("Failed to validate instructions. Please try again.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleExpandThread = async (threadId: string) => {
    if (expandedThreadId === threadId) { setExpandedThreadId(null); return; }
    setExpandedThreadId(threadId);
    if (!threadSteps[threadId]) {
      try {
        const steps = await api.getAdminThreadSteps(threadId);
        setThreadSteps((prev) => ({ ...prev, [threadId]: steps }));
      } catch (err) {
        console.error("Failed to load thread steps:", err);
      }
    }
  };

  const handleDeleteThread = async (threadId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("Delete this thread permanently? This cannot be undone.")) return;
    setDeletingThreadId(threadId);
    try {
      await api.deleteAdminThread(threadId);
      setAdminThreads((prev) => prev.filter((t) => t.id !== threadId));
      if (expandedThreadId === threadId) setExpandedThreadId(null);
      if (adminStats) {
        setAdminStats((prev) => prev ? { ...prev, total_threads: prev.total_threads - 1 } : prev);
      }
    } catch (err) {
      console.error("Failed to delete thread:", err);
    } finally {
      setDeletingThreadId(null);
    }
  };

  const groupedChatModels = groupModelOptions(CHAT_MODEL_OPTIONS);
  const groupedSttModels = groupModelOptions(STT_MODEL_OPTIONS);
  const groupedTtsModels = groupModelOptions(TTS_MODEL_OPTIONS);
  const groupedRealtimeModels = groupModelOptions(REALTIME_MODEL_OPTIONS);
  const ttsVoiceOptions = getVoiceOptionsForModel(ttsModel);
  const realtimeVoiceOptions = getVoiceOptionsForModel(DEFAULT_REALTIME_MODEL);

  const savedChatModel = getPreferredChatModel();
  const savedSttModel = getPreferredSTTModel();
  const savedTtsModel = getPreferredTTSModel();
  const savedTtsVoice = getPreferredTTSVoice(savedTtsModel);
  const savedTtsPlaybackRate = getPreferredTTSPlaybackRate();
  const savedRealtimeModel = getPreferredRealtimeModel();
  const savedRealtimeVoice = getPreferredRealtimeVoice();

  const hasUnsavedModelPreferences =
    chatModel !== savedChatModel || sttModel !== savedSttModel ||
    ttsModel !== savedTtsModel || ttsVoice !== savedTtsVoice ||
    ttsPlaybackRate !== savedTtsPlaybackRate || realtimeModel !== savedRealtimeModel ||
    realtimeVoice !== savedRealtimeVoice;

  const isDefaultModelPreferences =
    chatModel === DEFAULT_CHAT_MODEL && sttModel === DEFAULT_STT_MODEL &&
    ttsModel === DEFAULT_TTS_MODEL && ttsVoice === DEFAULT_TTS_VOICE &&
    ttsPlaybackRate === DEFAULT_TTS_PLAYBACK_RATE && realtimeModel === DEFAULT_REALTIME_MODEL &&
    realtimeVoice === DEFAULT_REALTIME_VOICE;

  const selectedChatModel = CHAT_MODEL_OPTIONS.find((o) => o.id === chatModel);
  const selectedSttModel = STT_MODEL_OPTIONS.find((o) => o.id === sttModel);
  const selectedTtsModel = TTS_MODEL_OPTIONS.find((o) => o.id === ttsModel);
  const selectedRealtimeModel = REALTIME_MODEL_OPTIONS.find((o) => o.id === realtimeModel);

  const handleSettingsTabChange = useCallback(
    (tab: SettingsTab) => { setActiveTab(tab); onTabChange?.(tab); },
    [onTabChange],
  );

  const handleSaveModelPreferences = () => {
    writeStoredValue(CHAT_MODEL_STORAGE_KEY, chatModel);
    writeStoredValue(STT_MODEL_STORAGE_KEY, sttModel);
    writeStoredValue(TTS_MODEL_STORAGE_KEY, ttsModel);
    writeStoredValue(TTS_VOICE_STORAGE_KEY, ttsVoice);
    writeStoredValue(TTS_PLAYBACK_RATE_STORAGE_KEY, String(ttsPlaybackRate));
    writeStoredValue(REALTIME_MODEL_STORAGE_KEY, realtimeModel);
    writeStoredValue(REALTIME_VOICE_STORAGE_KEY, realtimeVoice);
    setModelPreferencesNotice({ tone: "success", message: "Model and voice preferences saved." });
  };

  const handleResetModelPreferences = () => {
    setChatModel(DEFAULT_CHAT_MODEL);
    setSttModel(DEFAULT_STT_MODEL);
    setTtsModel(DEFAULT_TTS_MODEL);
    setTtsVoice(DEFAULT_TTS_VOICE);
    setTtsPlaybackRate(DEFAULT_TTS_PLAYBACK_RATE);
    setRealtimeModel(DEFAULT_REALTIME_MODEL);
    setRealtimeVoice(DEFAULT_REALTIME_VOICE);
    clearStoredValue(CHAT_MODEL_STORAGE_KEY);
    clearStoredValue(STT_MODEL_STORAGE_KEY);
    clearStoredValue(TTS_MODEL_STORAGE_KEY);
    clearStoredValue(TTS_VOICE_STORAGE_KEY);
    clearStoredValue(TTS_PLAYBACK_RATE_STORAGE_KEY);
    clearStoredValue(REALTIME_MODEL_STORAGE_KEY);
    clearStoredValue(REALTIME_VOICE_STORAGE_KEY);
    setModelPreferencesNotice({ tone: "info", message: "Model and voice preferences reset to defaults." });
  };

  const handleDisconnectSpotify = useCallback(async () => {
    setDisconnectingApp("spotify");
    try { await api.disconnectSpotify(); await checkAuth(); }
    catch (err) { console.error("Failed to disconnect Spotify:", err); }
    finally { setDisconnectingApp(null); }
  }, [checkAuth]);

  const handleDisconnectWorkspace = useCallback(async () => {
    setDisconnectingApp("workspace");
    try {
      await fetch("/api/workspace/token", { method: "DELETE" });
      await checkAuth();
    } catch (err) {
      console.error("Failed to disconnect Google Workspace:", err);
    } finally {
      setDisconnectingApp(null);
    }
  }, [checkAuth]);

  if (!isOpen) return null;

  const tabs: { id: SettingsTab; label: string; icon: React.ElementType }[] = [
    { id: "general", label: "General", icon: Settings },
    { id: "apps", label: "Apps", icon: Puzzle },
    ...(isAdmin ? [{ id: "admin" as SettingsTab, label: "Admin", icon: ShieldCheck }] : []),
  ];

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
        aria-label="Close settings"
      />

      <div className="relative flex h-full w-full items-center justify-center p-0 sm:p-6">
        <div
          className="relative flex h-full w-full max-w-3xl flex-col overflow-hidden border border-(--border) bg-background shadow-2xl sm:h-[min(85vh,720px)] sm:rounded-2xl"
          onClick={(event) => event.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label="Settings"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-(--border) px-6 py-4">
            <h2 className="text-lg font-semibold">Settings</h2>
            <button
              onClick={onClose}
              className="cursor-pointer rounded-lg p-1.5 text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground"
              aria-label="Close settings"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Tab bar */}
          <div className="border-b border-(--border) px-6">
            <div className="no-scrollbar -mb-px flex gap-1 overflow-x-auto">
              {tabs.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => handleSettingsTabChange(id)}
                  className={`inline-flex cursor-pointer items-center gap-2 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors ${
                    activeTab === id
                      ? "border-(--accent) text-foreground"
                      : "border-transparent text-(--muted) hover:text-foreground"
                  }`}
                  aria-current={activeTab === id ? "page" : undefined}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Content */}
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
            {activeTab === "general" && (
              <GeneralTab
                customInstructions={customInstructions}
                setCustomInstructions={setCustomInstructions}
                isSaving={isSaving}
                saveError={saveError}
                setSaveError={setSaveError}
                saveSuccess={saveSuccess}
                handleSaveInstructions={handleSaveInstructions}
                chatModel={chatModel}
                setChatModel={setChatModel}
                sttModel={sttModel}
                setSttModel={setSttModel}
                ttsModel={ttsModel}
                setTtsModel={setTtsModel}
                ttsVoice={ttsVoice}
                setTtsVoice={setTtsVoice}
                ttsPlaybackRate={ttsPlaybackRate}
                setTtsPlaybackRate={setTtsPlaybackRate}
                realtimeModel={realtimeModel}
                setRealtimeModel={setRealtimeModel}
                realtimeVoice={realtimeVoice}
                setRealtimeVoice={setRealtimeVoice}
                groupedChatModels={groupedChatModels}
                groupedSttModels={groupedSttModels}
                groupedTtsModels={groupedTtsModels}
                groupedRealtimeModels={groupedRealtimeModels}
                ttsVoiceOptions={ttsVoiceOptions}
                realtimeVoiceOptions={realtimeVoiceOptions}
                selectedChatModel={selectedChatModel}
                selectedSttModel={selectedSttModel}
                selectedTtsModel={selectedTtsModel}
                selectedRealtimeModel={selectedRealtimeModel}
                hasUnsavedModelPreferences={hasUnsavedModelPreferences}
                isDefaultModelPreferences={isDefaultModelPreferences}
                handleSaveModelPreferences={handleSaveModelPreferences}
                handleResetModelPreferences={handleResetModelPreferences}
                modelPreferencesNotice={modelPreferencesNotice}
                setModelPreferencesNotice={setModelPreferencesNotice}
              />
            )}

            {activeTab === "apps" && (
              <AppsTab
                googleAuth={googleAuth}
                spotifyAuth={spotifyAuth}
                workspaceAuth={workspaceAuth}
                loginWithGoogle={loginWithGoogle}
                loginWithSpotify={loginWithSpotify}
                loginWithWorkspace={loginWithWorkspace}
                handleDisconnectSpotify={handleDisconnectSpotify}
                handleDisconnectWorkspace={handleDisconnectWorkspace}
                disconnectingApp={disconnectingApp}
              />
            )}

            {activeTab === "admin" && isAdmin && (
              <AdminTab
                adminStats={adminStats}
                adminUsers={adminUsers}
                adminThreads={adminThreads}
                adminLoading={adminLoading}
                adminLoadNotice={adminLoadNotice}
                adminUsersError={adminUsersError}
                adminTab={adminTab}
                setAdminTab={setAdminTab}
                expandedThreadId={expandedThreadId}
                threadSteps={threadSteps}
                deletingThreadId={deletingThreadId}
                handleExpandThread={handleExpandThread}
                handleDeleteThread={handleDeleteThread}
                loadAdminData={loadAdminData}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
