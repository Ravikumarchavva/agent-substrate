"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  Archive,
  BarChart3,
  BrainCircuit,
  HardDrive,
  Puzzle,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import type {
  AdminStats,
  AdminStep,
  AdminStorageSession,
  AdminStorageUser,
  AdminThread,
  AdminUser,
  OpenAITTSVoice,
  TTSPlaybackRate,
  TTSVoice,
} from "@/types";
import {
  CHAT_MODEL_OPTIONS,
  CHAT_MODEL_STORAGE_KEY,
  DEFAULT_CHAT_MODEL,
  DEFAULT_REALTIME_MODEL,
  DEFAULT_REALTIME_VOICE,
  DEFAULT_STT_MODEL,
  DEFAULT_TTS_MODEL,
  DEFAULT_TTS_PLAYBACK_RATE,
  DEFAULT_TTS_VOICE,
  REALTIME_MODEL_OPTIONS,
  REALTIME_MODEL_STORAGE_KEY,
  REALTIME_VOICE_STORAGE_KEY,
  STT_MODEL_OPTIONS,
  STT_MODEL_STORAGE_KEY,
  TTS_MODEL_OPTIONS,
  TTS_MODEL_STORAGE_KEY,
  TTS_PLAYBACK_RATE_STORAGE_KEY,
  TTS_VOICE_STORAGE_KEY,
  clearStoredValue,
  getPreferredChatModel,
  getPreferredRealtimeModel,
  getPreferredRealtimeVoice,
  getPreferredSTTModel,
  getPreferredTTSModel,
  getPreferredTTSPlaybackRate,
  getPreferredTTSVoice,
  getVoiceOptionsForModel,
  groupModelOptions,
  writeStoredValue,
} from "@/lib/model-preferences";
import { GeneralTab } from "./GeneralTab";
import { ConnectorsTab } from "./ConnectorsTab";
import { ModelsTab } from "./ModelsTab";
import { AdminTab } from "./AdminTab";
import { StorageTab } from "./StorageTab";
import { PersonalizationTab } from "./PersonalizationTab";
import { UsageTab } from "./UsageTab";
import { ArchivedTab } from "./ArchivedTab";
import { confirmAction } from "@/design";
import { schedulePreferencesPush } from "@/lib/preferences-sync";
import { reportError } from "@/lib/report-error";

export type SettingsTab =
  | "general"
  | "memory"
  | "usage"
  | "archived"
  | "apps"
  | "llm"
  | "storage"
  | "admin";

interface SettingsNotice {
  tone: "success" | "info";
  message: string;
}

interface SettingsNavItem {
  id: SettingsTab;
  label: string;
  description: string;
  icon: LucideIcon;
  requiresAdmin?: boolean;
}

interface SettingsNavGroup {
  title: string;
  items: SettingsNavItem[];
}

export const SETTINGS_TAB_GROUPS: SettingsNavGroup[] = [
  {
    title: "Personal",
    items: [
      { id: "general", label: "General", description: "Timezone and prompt defaults", icon: Settings },
      { id: "memory", label: "Personalization", description: "Instructions and memories", icon: BrainCircuit },
      { id: "usage", label: "Usage", description: "Messages, tokens and cost", icon: BarChart3 },
      { id: "archived", label: "Archived", description: "Conversations you put away", icon: Archive },
      { id: "storage", label: "Storage", description: "Uploaded and generated files", icon: HardDrive },
    ],
  },
  {
    title: "Workspace",
    items: [
      { id: "apps", label: "Connectors", description: "Connected apps and catalog", icon: Puzzle },
      { id: "llm", label: "LLM Setup", description: "Model and voice defaults", icon: SlidersHorizontal },
    ],
  },
  {
    title: "Operations",
    items: [
      { id: "admin", label: "Admin", description: "Users, threads, and audit trails", icon: ShieldCheck, requiresAdmin: true },
    ],
  },
];

export function getVisibleSettingsTabGroups(isAdmin: boolean): SettingsNavGroup[] {
  return SETTINGS_TAB_GROUPS
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.requiresAdmin || isAdmin),
    }))
    .filter((group) => group.items.length > 0);
}

/** Tabs that are a full `Page` (own header, own scrolling) rather than a form in a padded column. */
const FULL_PAGE_TABS: SettingsTab[] = ["general", "memory", "usage", "archived", "storage", "apps", "llm", "admin"];


interface SettingsPanelProps {
  isOpen: boolean;
  initialTab?: SettingsTab;
  onTabChange?: (tab: SettingsTab) => void;
  /** Current conversation, so the Artifacts tab can show its session scope. */
  threadId?: string | null;
}

function getAsyncErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  if (typeof error === "string" && error.trim()) return error;
  return fallback;
}

export function SettingsPanel({
  isOpen,
  initialTab = "general",
  onTabChange,
  threadId,
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
  const [adminLoaded, setAdminLoaded] = useState(false);
  const [adminLoadNotice, setAdminLoadNotice] = useState<string | null>(null);
  const [adminUsersError, setAdminUsersError] = useState<string | null>(null);
  const [adminTab, setAdminTab] = useState<"users" | "threads" | "storage">("users");
  const [expandedThreadId, setExpandedThreadId] = useState<string | null>(null);
  const [threadSteps, setThreadSteps] = useState<Record<string, AdminStep[]>>({});
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);

  const [adminStorageUsers, setAdminStorageUsers] = useState<AdminStorageUser[]>([]);
  const [adminStorageLoading, setAdminStorageLoading] = useState(false);
  const [adminStorageLoaded, setAdminStorageLoaded] = useState(false);
  const [adminStorageSessions, setAdminStorageSessions] = useState<
    Record<string, AdminStorageSession[]>
  >({});
  const [expandedStorageUserId, setExpandedStorageUserId] = useState<string | null>(null);
  const [savingQuotaUserId, setSavingQuotaUserId] = useState<string | null>(null);

  const syncLocalSettings = useCallback(() => {

    const preferredTtsModel = getPreferredTTSModel();
    setChatModel(getPreferredChatModel());
    setSttModel(getPreferredSTTModel());
    setTtsModel(preferredTtsModel);
    setTtsVoice(getPreferredTTSVoice(preferredTtsModel));
    setTtsPlaybackRate(getPreferredTTSPlaybackRate());
    setRealtimeModel(getPreferredRealtimeModel());

    setModelPreferencesNotice(null);
  }, []);

  useEffect(() => {
    if (isOpen) syncLocalSettings();
  }, [isOpen, syncLocalSettings]);

  useEffect(() => {
    if (isOpen) setActiveTab(initialTab);
  }, [initialTab, isOpen]);

  useEffect(() => {
    if (!isAdmin && activeTab === "admin") {
      setActiveTab("general");
      onTabChange?.("general");
    }
  }, [activeTab, isAdmin, onTabChange]);

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
      setAdminLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "admin" && isAdmin && !adminLoading && !adminLoaded) {
      void loadAdminData();
    }
  }, [activeTab, adminLoading, adminLoaded, isAdmin, loadAdminData]);

  const loadAdminStorage = useCallback(async () => {
    setAdminStorageLoading(true);
    try {
      setAdminStorageUsers(await api.getAdminStorageUsers());
    } catch (error) {
      console.warn("Failed to load admin storage.", error);
    } finally {
      setAdminStorageLoading(false);
      setAdminStorageLoaded(true);
    }
  }, []);

  // Separate from loadAdminData's batch — a distinct backend surface
  // (agent-substrate's WorkspaceFileStore-only /admin/storage routes, 501
  // for non-local FILE_STORE_BACKEND) that shouldn't block or be blocked by
  // threads/users/stats.
  useEffect(() => {
    if (
      activeTab === "admin" &&
      adminTab === "storage" &&
      isAdmin &&
      !adminStorageLoading &&
      !adminStorageLoaded
    ) {
      void loadAdminStorage();
    }
  }, [activeTab, adminTab, adminStorageLoading, adminStorageLoaded, isAdmin, loadAdminStorage]);

  const handleExpandStorageUser = useCallback(
    (userId: string) => {
      setExpandedStorageUserId((prev) => (prev === userId ? null : userId));
      if (!adminStorageSessions[userId]) {
        void api
          .getAdminStorageSessions(userId)
          .then((sessions) =>
            setAdminStorageSessions((prev) => ({ ...prev, [userId]: sessions })),
          )
          .catch((error) => console.warn("Failed to load storage sessions.", error));
      }
    },
    [adminStorageSessions],
  );

  const handleSaveQuota = useCallback((userId: string, quotaBytes: number | null) => {
    setSavingQuotaUserId(userId);
    void api
      .setAdminStorageQuota(userId, quotaBytes)
      .then((updated) =>
        setAdminStorageUsers((prev) =>
          prev.map((u) =>
            u.user_id === userId ? { ...u, quota_bytes: updated.quota_bytes } : u,
          ),
        ),
      )
      .catch((error) => console.warn("Failed to set storage quota.", error))
      .finally(() => setSavingQuotaUserId(null));
  }, []);

  const handleExpandThread = async (threadId: string) => {
    if (expandedThreadId === threadId) {
      setExpandedThreadId(null);
      return;
    }

    setExpandedThreadId(threadId);
    if (!threadSteps[threadId]) {
      try {
        const steps = await api.getAdminThreadSteps(threadId);
        setThreadSteps((prev) => ({ ...prev, [threadId]: steps }));
      } catch (err) {
        reportError("Couldn't load this conversation's steps", err);
      }
    }
  };

  const handleDeleteThread = async (threadId: string, event: React.MouseEvent) => {
    event.stopPropagation();
    if (!(await confirmAction({ title: "Delete this conversation?", description: "It is removed permanently. This can't be undone.", confirmLabel: "Delete", danger: true }))) return;

    setDeletingThreadId(threadId);
    try {
      await api.deleteAdminThread(threadId);
      setAdminThreads((prev) => prev.filter((thread) => thread.id !== threadId));
      if (expandedThreadId === threadId) setExpandedThreadId(null);
      if (adminStats) {
        setAdminStats((prev) => (prev ? { ...prev, total_threads: prev.total_threads - 1 } : prev));
      }
    } catch (err) {
      reportError("Couldn't delete the conversation", err);
    } finally {
      setDeletingThreadId(null);
    }
  };

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
    try {
      await api.disconnectSpotify();
      await checkAuth();
    } catch (err) {
      reportError("Couldn't disconnect Spotify", err);
    } finally {
      setDisconnectingApp(null);
    }
  }, [checkAuth]);

  const handleDisconnectWorkspace = useCallback(async () => {
    setDisconnectingApp("workspace");
    try {
      await api.disconnectWorkspace();
      await checkAuth();
    } catch (err) {
      reportError("Couldn't disconnect Google Workspace", err);
    } finally {
      setDisconnectingApp(null);
    }
  }, [checkAuth]);






  if (!isOpen) return null;

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
    chatModel !== savedChatModel ||
    sttModel !== savedSttModel ||
    ttsModel !== savedTtsModel ||
    ttsVoice !== savedTtsVoice ||
    ttsPlaybackRate !== savedTtsPlaybackRate ||
    realtimeModel !== savedRealtimeModel ||
    realtimeVoice !== savedRealtimeVoice;

  const isDefaultModelPreferences =
    chatModel === DEFAULT_CHAT_MODEL &&
    sttModel === DEFAULT_STT_MODEL &&
    ttsModel === DEFAULT_TTS_MODEL &&
    ttsVoice === DEFAULT_TTS_VOICE &&
    ttsPlaybackRate === DEFAULT_TTS_PLAYBACK_RATE &&
    realtimeModel === DEFAULT_REALTIME_MODEL &&
    realtimeVoice === DEFAULT_REALTIME_VOICE;

  const selectedChatModel = CHAT_MODEL_OPTIONS.find((option) => option.id === chatModel);
  const selectedSttModel = STT_MODEL_OPTIONS.find((option) => option.id === sttModel);
  const selectedTtsModel = TTS_MODEL_OPTIONS.find((option) => option.id === ttsModel);
  const selectedRealtimeModel = REALTIME_MODEL_OPTIONS.find((option) => option.id === realtimeModel);

  const handleInlineTabChange = (tab: SettingsTab) => {
    setActiveTab(tab);
    onTabChange?.(tab);
  };

  return (
    <div className={`flex flex-col ${FULL_PAGE_TABS.includes(activeTab) ? "h-full flex-1 min-h-0" : "min-h-full"}`}>
      <div
        className={
          FULL_PAGE_TABS.includes(activeTab)
            ? "flex-1 min-h-0 h-full w-full flex flex-col"
            : "mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-10 lg:py-10"
        }
      >
        {activeTab === "general" && <GeneralTab onOpenTab={handleInlineTabChange} />}

        {activeTab === "apps" && (
          <ConnectorsTab
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

        {activeTab === "llm" && (
          <ModelsTab
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


        {activeTab === "memory" && <PersonalizationTab />}

        {activeTab === "usage" && <UsageTab />}

        {activeTab === "archived" && <ArchivedTab />}

        {activeTab === "storage" && <StorageTab />}


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
            adminStorageUsers={adminStorageUsers}
            adminStorageLoading={adminStorageLoading}
            adminStorageSessions={adminStorageSessions}
            expandedStorageUserId={expandedStorageUserId}
            handleExpandStorageUser={handleExpandStorageUser}
            savingQuotaUserId={savingQuotaUserId}
            handleSaveQuota={handleSaveQuota}
          />
        )}
      </div>
    </div>
  );
}