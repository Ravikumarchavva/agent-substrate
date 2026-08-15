"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  BrainCircuit,
  HardDrive,
  Puzzle,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
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
import { SearchTab } from "./SearchTab";
import { AdminTab } from "./AdminTab";
import { StorageTab } from "./StorageTab";
import { MemoryTab } from "./MemoryTab";

export type SettingsTab =
  | "general"
  | "apps"
  | "llm"
  | "search"
  | "storage"
  | "memory"
  | "admin";

interface SettingsNotice {
  tone: "success" | "info";
  message: string;
}

interface SettingsNavItem {
  id: SettingsTab;
  label: string;
  description: string;
  icon: React.ElementType;
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
      { id: "memory", label: "Memory", description: "What the assistant remembers about you", icon: BrainCircuit },
      { id: "storage", label: "Storage", description: "Uploaded and generated files", icon: HardDrive },
    ],
  },
  {
    title: "Workspace",
    items: [
      { id: "apps", label: "Connectors", description: "Connected apps and catalog", icon: Puzzle },
      { id: "llm", label: "LLM Setup", description: "Model and voice defaults", icon: SlidersHorizontal },
      { id: "search", label: "Search", description: "Retrieval and reranking preview", icon: Search },
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

const CUSTOM_INSTRUCTIONS_STORAGE_KEY = "system_instructions_override";
const TIMEZONE_STORAGE_KEY = "user_timezone";
const SEARCH_EMBEDDING_MODEL_STORAGE_KEY = "search_embedding_model";
const SEARCH_RERANKER_MODEL_STORAGE_KEY = "search_reranker_model";
const SEARCH_RERANK_LIMIT_STORAGE_KEY = "search_rerank_limit";
const SEARCH_CONTEXTUAL_RAG_STORAGE_KEY = "search_contextual_rag";
const SEARCH_MULTIPASS_STORAGE_KEY = "search_multipass_indexing";

interface SettingsPanelProps {
  isOpen: boolean;
  initialTab?: SettingsTab;
  onTabChange?: (tab: SettingsTab) => void;
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
  const [timezone, setTimezone] = useState("");
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

  const [embeddingModel, setEmbeddingModel] = useState("embed-english-light-v3.0");
  const [rerankerModel, setRerankerModel] = useState("mixedbread-base");
  const [rerankLimit, setRerankLimit] = useState(20);
  const [contextualRag, setContextualRag] = useState(false);
  const [multipassIndexing, setMultipassIndexing] = useState(false);

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
    setTimezone(localStorage.getItem(TIMEZONE_STORAGE_KEY) ?? "");

    const preferredTtsModel = getPreferredTTSModel();
    setChatModel(getPreferredChatModel());
    setSttModel(getPreferredSTTModel());
    setTtsModel(preferredTtsModel);
    setTtsVoice(getPreferredTTSVoice(preferredTtsModel));
    setTtsPlaybackRate(getPreferredTTSPlaybackRate());
    setRealtimeModel(getPreferredRealtimeModel());
    setRealtimeVoice(getPreferredRealtimeVoice());

    setEmbeddingModel(localStorage.getItem(SEARCH_EMBEDDING_MODEL_STORAGE_KEY) ?? "embed-english-light-v3.0");
    setRerankerModel(localStorage.getItem(SEARCH_RERANKER_MODEL_STORAGE_KEY) ?? "mixedbread-base");
    setRerankLimit(Number.parseInt(localStorage.getItem(SEARCH_RERANK_LIMIT_STORAGE_KEY) ?? "20", 10));
    setContextualRag(localStorage.getItem(SEARCH_CONTEXTUAL_RAG_STORAGE_KEY) === "true");
    setMultipassIndexing(localStorage.getItem(SEARCH_MULTIPASS_STORAGE_KEY) === "true");

    setSaveError(null);
    setSaveSuccess(false);
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
        console.error("Failed to load thread steps:", err);
      }
    }
  };

  const handleDeleteThread = async (threadId: string, event: React.MouseEvent) => {
    event.stopPropagation();
    if (!confirm("Delete this thread permanently? This cannot be undone.")) return;

    setDeletingThreadId(threadId);
    try {
      await api.deleteAdminThread(threadId);
      setAdminThreads((prev) => prev.filter((thread) => thread.id !== threadId));
      if (expandedThreadId === threadId) setExpandedThreadId(null);
      if (adminStats) {
        setAdminStats((prev) => (prev ? { ...prev, total_threads: prev.total_threads - 1 } : prev));
      }
    } catch (err) {
      console.error("Failed to delete thread:", err);
    } finally {
      setDeletingThreadId(null);
    }
  };

  const handleTimezoneChange = (value: string) => {
    setTimezone(value);
    if (value.trim()) localStorage.setItem(TIMEZONE_STORAGE_KEY, value.trim());
    else localStorage.removeItem(TIMEZONE_STORAGE_KEY);
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
      console.error("Failed to disconnect Spotify:", err);
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
      console.error("Failed to disconnect Google Workspace:", err);
    } finally {
      setDisconnectingApp(null);
    }
  }, [checkAuth]);

  useEffect(() => {
    localStorage.setItem(SEARCH_EMBEDDING_MODEL_STORAGE_KEY, embeddingModel);
  }, [embeddingModel]);

  useEffect(() => {
    localStorage.setItem(SEARCH_RERANKER_MODEL_STORAGE_KEY, rerankerModel);
  }, [rerankerModel]);

  useEffect(() => {
    localStorage.setItem(SEARCH_RERANK_LIMIT_STORAGE_KEY, String(rerankLimit));
  }, [rerankLimit]);

  useEffect(() => {
    localStorage.setItem(SEARCH_CONTEXTUAL_RAG_STORAGE_KEY, String(contextualRag));
  }, [contextualRag]);

  useEffect(() => {
    localStorage.setItem(SEARCH_MULTIPASS_STORAGE_KEY, String(multipassIndexing));
  }, [multipassIndexing]);

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
  const tabGroups = getVisibleSettingsTabGroups(isAdmin);

  const handleInlineTabChange = (tab: SettingsTab) => {
    setActiveTab(tab);
    onTabChange?.(tab);
  };

  return (
    <div className="flex min-h-full flex-col">
      <div className="border-b border-(--border) px-4 py-3 lg:hidden">
        <div className="flex gap-2 overflow-x-auto pb-1">
          {tabGroups.flatMap((group) => group.items).map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => handleInlineTabChange(id)}
              className={`shrink-0 rounded-full px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${activeTab === id ? "bg-foreground text-background" : "bg-(--card) text-(--muted) hover:text-foreground"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-10 lg:py-10">
        {activeTab === "general" && (
          <GeneralTab
            customInstructions={customInstructions}
            setCustomInstructions={setCustomInstructions}
            isSaving={isSaving}
            saveError={saveError}
            setSaveError={setSaveError}
            saveSuccess={saveSuccess}
            handleSaveInstructions={handleSaveInstructions}
            timezone={timezone}
            onTimezoneChange={handleTimezoneChange}
          />
        )}

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

        {activeTab === "search" && (
          <SearchTab
            embeddingModel={embeddingModel}
            setEmbeddingModel={setEmbeddingModel}
            rerankerModel={rerankerModel}
            setRerankerModel={setRerankerModel}
            rerankLimit={rerankLimit}
            setRerankLimit={setRerankLimit}
            contextualRag={contextualRag}
            setContextualRag={setContextualRag}
            multipassIndexing={multipassIndexing}
            setMultipassIndexing={setMultipassIndexing}
          />
        )}

        {activeTab === "storage" && <StorageTab />}

        {activeTab === "memory" && <MemoryTab />}

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
  );
}