"use client";

import { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from "react";
import type { AuthUser } from "@/types";

type AuthContextType = {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  isLoading: boolean;
  googleAuth: boolean;
  spotifyAuth: boolean;
  workspaceAuth: boolean;
  loginWithGoogle: () => void;
  loginWithSpotify: () => void;
  loginWithWorkspace: () => void;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

interface SessionResponse {
  authenticated: boolean;
  user: { id: string; email: string; name: string | null; avatarUrl: string | null; isAdmin: boolean } | null;
}

async function fetchSession(): Promise<SessionResponse> {
  try {
    const res = await fetch("/chat/api/auth/session", { credentials: "include" });
    if (!res.ok) return { authenticated: false, user: null };
    return (await res.json()) as SessionResponse;
  } catch {
    return { authenticated: false, user: null };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [googleAuth, setGoogleAuth] = useState(false);
  const [spotifyAuth, setSpotifyAuth] = useState(false);
  const [workspaceAuth, setWorkspaceAuth] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const checkAuthPromiseRef = useRef<Promise<void> | null>(null);

  const checkAuth = useCallback(async () => {
    if (checkAuthPromiseRef.current) {
      return checkAuthPromiseRef.current;
    }

    const promise = (async () => {
      setIsLoading(true);

      let nextUser: AuthUser | null = null;
      let nextGoogleAuth = false;
      let nextSpotifyAuth = false;
      let nextWorkspaceAuth = false;

      try {
        // The server-verified session is the only source of truth for who is
        // logged in — never trust a client-readable cookie for identity.
        let sessionData = await fetchSession();

        // No local session yet — check whether the platform
        // (agent-substrate-platform) already has one. Same origin means its
        // Auth.js session cookie is already on this request; /api/auth/platform
        // asks the platform to confirm it and, if valid, creates the local
        // DB-backed session, so re-checking /api/auth/session picks it up.
        if (!sessionData.authenticated) {
          const platformRes = await fetch("/chat/api/auth/platform", {
            credentials: "include",
          });
          if (platformRes.ok) {
            const platformData = (await platformRes.json()) as { authenticated?: boolean };
            if (platformData.authenticated) {
              sessionData = await fetchSession();
            }
          }
        }

        if (sessionData.authenticated && sessionData.user) {
          nextGoogleAuth = true;
          nextUser = {
            email: sessionData.user.email,
            name: sessionData.user.name ?? undefined,
            picture: sessionData.user.avatarUrl ?? undefined,
            isAdmin: sessionData.user.isAdmin,
          };
        }

        if (nextGoogleAuth) {
          const [spotifyRes, workspaceRes] = await Promise.all([
            fetch("/chat/api/spotify/token", { credentials: "include" }),
            fetch("/chat/api/workspace/token", { credentials: "include" }),
          ]);

          if (spotifyRes.ok) {
            const spotifyData = (await spotifyRes.json()) as { authenticated?: boolean; access_token?: string };
            if (spotifyData.authenticated || spotifyData.access_token) {
              nextSpotifyAuth = true;
            }
          }

          if (workspaceRes.ok) {
            const wsData = (await workspaceRes.json()) as { connected?: boolean; access_token?: string };
            if (wsData.connected || wsData.access_token) {
              nextWorkspaceAuth = true;
            }
          }
        }
      } catch (err) {
        console.error("Auth check failed:", err);
      } finally {
        setUser(nextUser);
        setGoogleAuth(nextGoogleAuth);
        setSpotifyAuth(nextSpotifyAuth);
        setWorkspaceAuth(nextWorkspaceAuth);
        setIsLoading(false);
      }
    })().finally(() => {
      checkAuthPromiseRef.current = null;
    });

    checkAuthPromiseRef.current = promise;
    return promise;
  }, []);

  const loginWithGoogle = () => {
    const width = 500;
    const height = 700;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;

    const popup = window.open(
      "/chat/api/auth/google/login",
      "google-auth",
      `width=${width},height=${height},left=${left},top=${top}`
    );

    // Listen for postMessage from callback page (faster than polling)
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === "google_auth_success" || event.data?.type === "google_auth_error") {
        window.removeEventListener("message", onMessage);
        clearInterval(pollTimer);
        popup?.close();
        void checkAuth();
      }
    };
    window.addEventListener("message", onMessage);

    // Fallback: poll for popup close
    const pollTimer = setInterval(() => {
      if (popup?.closed) {
        clearInterval(pollTimer);
        window.removeEventListener("message", onMessage);
        void checkAuth();
      }
    }, 500);
  };

  const loginWithWorkspace = () => {
    const width = 500;
    const height = 700;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;

    const popup = window.open(
      "/chat/api/workspace/login",
      "workspace-auth",
      `width=${width},height=${height},left=${left},top=${top}`
    );

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === "workspace_auth_success" || event.data?.type === "workspace_auth_error") {
        window.removeEventListener("message", onMessage);
        clearInterval(pollTimer);
        popup?.close();
        void checkAuth();
      }
    };
    window.addEventListener("message", onMessage);

    const pollTimer = setInterval(() => {
      if (popup?.closed) {
        clearInterval(pollTimer);
        window.removeEventListener("message", onMessage);
        void checkAuth();
      }
    }, 500);
  };

  const loginWithSpotify = () => {
    const width = 500;
    const height = 700;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;

    const popup = window.open(
      "/chat/api/spotify/login",
      "spotify-auth",
      `width=${width},height=${height},left=${left},top=${top}`
    );

    // Listen for postMessage from callback page (faster than polling)
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === "spotify_auth_success" || event.data?.type === "spotify_auth_error") {
        window.removeEventListener("message", onMessage);
        clearInterval(pollTimer);
        popup?.close();
        void checkAuth();
      }
    };
    window.addEventListener("message", onMessage);

    // Fallback: poll for popup close
    const pollTimer = setInterval(() => {
      if (popup?.closed) {
        clearInterval(pollTimer);
        window.removeEventListener("message", onMessage);
        void checkAuth();
      }
    }, 500);
  };

  const logout = async () => {
    try {
      await fetch("/chat/api/auth/google/logout", {
        method: "POST",
        credentials: "include",
      });

      if (spotifyAuth) {
        await fetch("/chat/api/spotify/token", {
          method: "DELETE",
          credentials: "include",
        });
      }

      if (workspaceAuth) {
        await fetch("/chat/api/workspace/token", {
          method: "DELETE",
          credentials: "include",
        });
      }

      setUser(null);
      setGoogleAuth(false);
      setSpotifyAuth(false);
      setWorkspaceAuth(false);
    } catch (err) {
      console.error("Logout failed:", err);
    }
  };

  useEffect(() => {
    void checkAuth();
  }, [checkAuth]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: googleAuth,
        isAdmin: !!user?.isAdmin,
        isLoading,
        googleAuth,
        spotifyAuth,
        workspaceAuth,
        loginWithGoogle,
        loginWithSpotify,
        loginWithWorkspace,
        logout,
        checkAuth,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
