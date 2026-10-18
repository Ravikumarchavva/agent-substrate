"use client";

import { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from "react";
import type { AuthUser } from "@/types";

type AuthContextType = {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  isLoading: boolean;
  spotifyAuth: boolean;
  workspaceAuth: boolean;
  login: (organization?: string) => void;
  loginWithSpotify: () => void;
  loginWithWorkspace: () => void;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

interface SessionResponse {
  authenticated: boolean;
  user: { id: string; email: string; name: string | null; isAdmin: boolean; tenantId: string } | null;
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
  const [authenticated, setAuthenticated] = useState(false);
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
      let nextAuthenticated = false;
      let nextSpotifyAuth = false;
      let nextWorkspaceAuth = false;

      try {
        // The server-verified session is the only source of truth for who is
        // logged in — never trust a client-readable cookie for identity.
        const sessionData = await fetchSession();

        if (sessionData.authenticated && sessionData.user) {
          nextAuthenticated = true;
          nextUser = {
            email: sessionData.user.email,
            name: sessionData.user.name ?? undefined,
            isAdmin: sessionData.user.isAdmin,
            tenantId: sessionData.user.tenantId,
          };
        }

        if (nextAuthenticated) {
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
        setAuthenticated(nextAuthenticated);
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

  // Sign-in is a full-page trip to Keycloak (authorization code + PKCE, handled by the server) and back to where the user was.
  // `organization` signs in to that project's organization; the tenant of every request follows it.
  const login = (organization?: string) => {
    const params = new URLSearchParams({ next: window.location.pathname + window.location.search });
    if (organization) params.set("org", organization);
    window.location.assign(`/chat/auth/login?${params.toString()}`);
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
    } catch (err) {
      console.error("Disconnecting connectors failed:", err);
    }
    // Ends the Keycloak single sign-on session too, so one sign-out covers every Substrate app.
    window.location.assign("/chat/auth/logout");
  };

  useEffect(() => {
    void checkAuth();
  }, [checkAuth]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: authenticated,
        isAdmin: !!user?.isAdmin,
        isLoading,
        spotifyAuth,
        workspaceAuth,
        login,
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
