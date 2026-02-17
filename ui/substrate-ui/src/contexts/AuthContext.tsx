"use client";

import { createContext, useContext, useState, useEffect, ReactNode } from "react";

type User = {
  email?: string;
  name?: string;
  picture?: string;
  provider: "google" | "spotify";
};

type AuthContextType = {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  googleAuth: boolean;
  spotifyAuth: boolean;
  loginWithGoogle: () => void;
  loginWithSpotify: () => void;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [googleAuth, setGoogleAuth] = useState(false);
  const [spotifyAuth, setSpotifyAuth] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const checkAuth = async () => {
    setIsLoading(true);
    try {
      // Check Google auth
      const googleRes = await fetch("http://127.0.0.1:3001/api/auth/google/token", {
        credentials: "include",
      });
      if (googleRes.ok) {
        const googleData = await googleRes.json();
        if (googleData.authenticated) {
          setGoogleAuth(true);
          // Try to get user info from cookie
          const userCookie = document.cookie
            .split("; ")
            .find((row) => row.startsWith("google_user="));
          if (userCookie) {
            try {
              const userData = JSON.parse(decodeURIComponent(userCookie.split("=")[1]));
              setUser({ ...userData, provider: "google" });
            } catch (e) {
              console.error("Failed to parse user cookie:", e);
            }
          }
        }
      }

      // Check Spotify auth
      const spotifyRes = await fetch("http://127.0.0.1:3001/api/spotify/token", {
        credentials: "include",
      });
      if (spotifyRes.ok) {
        const spotifyData = await spotifyRes.json();
        if (spotifyData.authenticated) {
          setSpotifyAuth(true);
          // If no Google user, show Spotify as primary
          if (!user) {
            setUser({ name: "Spotify User", provider: "spotify" });
          }
        }
      }
    } catch (err) {
      console.error("Auth check failed:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const loginWithGoogle = () => {
    const width = 500;
    const height = 700;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;
    
    const popup = window.open(
      "http://127.0.0.1:3001/api/auth/google/login",
      "google-auth",
      `width=${width},height=${height},left=${left},top=${top}`
    );

    // Poll for popup close
    const pollTimer = setInterval(() => {
      if (popup?.closed) {
        clearInterval(pollTimer);
        checkAuth();
      }
    }, 500);
  };

  const loginWithSpotify = () => {
    const width = 500;
    const height = 700;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;
    
    const popup = window.open(
      "http://127.0.0.1:3001/api/spotify/login",
      "spotify-auth",
      `width=${width},height=${height},left=${left},top=${top}`
    );

    // Poll for popup close
    const pollTimer = setInterval(() => {
      if (popup?.closed) {
        clearInterval(pollTimer);
        checkAuth();
      }
    }, 500);
  };

  const logout = async () => {
    try {
      // Logout from both services
      if (googleAuth) {
        await fetch("http://127.0.0.1:3001/api/auth/google/logout", {
          method: "POST",
          credentials: "include",
        });
      }
      if (spotifyAuth) {
        await fetch("http://127.0.0.1:3001/api/spotify/logout", {
          method: "POST",
          credentials: "include",
        });
      }
      setUser(null);
      setGoogleAuth(false);
      setSpotifyAuth(false);
    } catch (err) {
      console.error("Logout failed:", err);
    }
  };

  // Check auth on mount
  useEffect(() => {
    checkAuth();
  }, []);

  // Listen for auth messages from popup
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.data.type === "google_auth_success") {
        checkAuth();
      } else if (event.data.type === "spotify_auth_success") {
        checkAuth();
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: googleAuth || spotifyAuth,
        isLoading,
        googleAuth,
        spotifyAuth,
        loginWithGoogle,
        loginWithSpotify,
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
